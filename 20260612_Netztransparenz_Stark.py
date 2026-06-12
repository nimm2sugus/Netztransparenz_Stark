import os
import io
import requests
import datetime
import pandas as pd
import numpy as np
import plotly.express as px
import streamlit as st

# Setup page configuration
st.set_page_config(
    page_title="Grid & Charging Station Analyzer",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🔌 Grid Portfolio & Charging Network Analyzer")
st.write(
    "Examine correlations across the entire German electricity system. "
    "Compare prices, actual generation forecasts, balancing reserves, and redispatch actions."
)

# Initialize a runtime log to trace API actions
logs = []

# --- Sidebar: Configuration ---
st.sidebar.header("1. Connection Settings")
use_demo = st.sidebar.checkbox("Use Demo Mode (Simulated Data)", value=True)

# Credentials inputs (auto-checks environment variables first)
client_id_env = os.environ.get('IPNT_CLIENT_ID', '')
client_secret_env = os.environ.get('IPNT_CLIENT_SECRET', '')

client_id = st.sidebar.text_input("Client ID", value=client_id_env, type="password")
client_secret = st.sidebar.text_input("Client Secret", value=client_secret_env, type="password")

st.sidebar.header("2. Energy System Parameters")

# Expanded dictionary mapping the full suite of Netztransparenz endpoints
endpoint_options = {
    # Market & Prices
    "Spot Market Prices (ct/kWh)": {"data": "Spotmarktpreise", "product": "none"},
    "Imbalance Price Index ID-AEP": {"data": "IdAep", "product": "none"},
    
    # Grid Status & Traffic Light
    "Grid Traffic Light (Green=1, Yellow=2, Red=3)": {"data": "TrafficLight", "product": "none"},
    
    # Generation Forecasts (Online-Hochrechnung)
    "Solar Online Forecast (MW)": {"data": "onlineHochrechnung", "product": "Solar"},
    "Wind Onshore Online Forecast (MW)": {"data": "onlineHochrechnung", "product": "Windonshore"},
    "Wind Offshore Online Forecast (MW)": {"data": "onlineHochrechnung", "product": "Windoffshore"},
    
    # Grid Balancing Reserves (Saldos)
    "Grid Balance NRV-Saldo (Betrieblich)": {"data": "nrvsaldo/NRVSaldo", "product": "Betrieblich"},
    "Grid Balance NRV-Saldo (Qualitätsgesichert)": {"data": "nrvsaldo/NRVSaldo", "product": "Qualitaetsgesichert"},
    "Grid Balance RZ-Saldo (Betrieblich)": {"data": "nrvsaldo/RZSaldo", "product": "Betrieblich"},
    "Activated aFRR (Betrieblich)": {"data": "nrvsaldo/AktivierteSRL", "product": "Betrieblich"},
    
    # System Interventions
    "Redispatch Measures (MW)": {"data": "redispatch", "product": "none"},
    "Curative Redispatch (MW)": {"data": "VorhaltungkRD", "product": "none"},
    "Capacity Reserve (Kapazitätsreserve)": {"data": "Kapazitaetsreserve", "product": "none"},
}

selected_metrics = st.sidebar.multiselect(
    "Select Metrics to Display", 
    options=list(endpoint_options.keys()),
    default=["Spot Market Prices (ct/kWh)", "Solar Online Forecast (MW)"]
)

# Date selection (Defaults to past dates to ensure historical data exists)
today = datetime.date.today()
date_from = st.sidebar.date_input("Start Date", today - datetime.timedelta(days=7))
date_to = st.sidebar.date_input("End Date", today - datetime.timedelta(days=3))

if date_to > today:
    st.sidebar.warning("⚠️ Selected dates include future dates. Real-time historical grid data may not exist yet.")

if date_from > date_to:
    st.error("Error: Start Date must be before or equal to End Date.")

# --- Authentication Function ---
@st.cache_data(ttl=3500)  # Cache token for 1 hour
def fetch_token(cid, secret):
    url = "https://identity.netztransparenz.de/users/connect/token"
    payload = {
        'grant_type': 'client_credentials',
        'client_id': cid,
        'client_secret': secret
    }
    try:
        res = requests.post(url, data=payload, timeout=10)
        if res.ok:
            return res.json().get('access_token'), None
        else:
            err = f"Auth failed (Code {res.status_code}): {res.reason} - {res.text}"
            return None, err
    except Exception as e:
        err = f"Auth connection error: {str(e)}"
        return None, err

# --- Helper: Defensive Time Normalization Engine ---
def extract_timeline(df):
    """Dynamically locates and normalizes temporal data inside any dataframe."""
    cols_lower = [c.lower() for c in df.columns]
    
    # Case 1: Standard 'Timestamp' already exists
    if "timestamp" in cols_lower:
        orig_col = df.columns[cols_lower.index("timestamp")]
        df["Timestamp"] = pd.to_datetime(df[orig_col], errors='coerce')
        return df
        
    # Case 2: standard German energy format ('Datum' and 'von')
    if "datum" in cols_lower and "von" in cols_lower:
        idx_d = df.columns[cols_lower.index("datum")]
        idx_v = df.columns[cols_lower.index("von")]
        df["Timestamp"] = pd.to_datetime(
            df[idx_d].astype(str) + " " + df[idx_v].astype(str), 
            format="%d.%m.%Y %H:%M", 
            errors='coerce'
        )
        return df
        
    # Case 3: Single datetime column matching generic keywords
    for key in ["date", "datetime", "zeit", "datum"]:
        if key in cols_lower:
            orig_col = df.columns[cols_lower.index(key)]
            df["Timestamp"] = pd.to_datetime(df[orig_col], errors='coerce')
            return df
            
    # Case 4: No keyword found. Check columns to see if one acts as a datetime series
    for col in df.columns:
        try:
            parsed = pd.to_datetime(df[col], errors='coerce')
            # If at least half the series parses as datetime, assume it's the timeline
            if parsed.notna().sum() > (len(df) * 0.5):
                df["Timestamp"] = parsed
                return df
        except:
            pass
            
    return df

# --- Cached Data Fetching Function ---
@st.cache_data(ttl=600)  # Cache individual queries for 10 minutes
def fetch_single_metric(data_param, product_param, start, end, token):
    str_start = start.strftime("%Y-%m-%d")
    str_end = end.strftime("%Y-%m-%d")
    headers = {'Authorization': f'Bearer {token}'}
    
    urls_to_try = []
    if product_param == "none":
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/{str_start}/{str_end}")
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/-/{str_start}/{str_end}")
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/none/{str_start}/{str_end}")
    else:
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/{product_param}/{str_start}/{str_end}")
        
    for url in urls_to_try:
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.ok:
                if not res.text or not res.text.strip():
                    continue
                
                # Semicolon-Separated German CSV Parsing
                if ";" in res.text or "Datum" in res.text:
                    df = pd.read_csv(io.StringIO(res.text), sep=";", decimal=",")
                    df = extract_timeline(df)
                    return df, None
                
                # Standard JSON Parsing
                raw_json = res.json()
                df_json = pd.DataFrame()
                if isinstance(raw_json, list):
                    df_json = pd.DataFrame(raw_json)
                elif isinstance(raw_json, dict):
                    for key in ["data", "values", "responseData"]:
                        if key in raw_json and isinstance(raw_json[key], list):
                            df_json = pd.DataFrame(raw_json[key])
                            break
                    if df_json.empty:
                        df_json = pd.DataFrame([raw_json])
                
                df_json = extract_timeline(df_json)
                return df_json, None
        except Exception as e:
            pass
            
    return None, f"Could not fetch or parse data for {data_param}"

# --- Mock Data Generator (Python 3.14 Compatible) ---
def generate_mock_data(metric, start, end):
    date_range = pd.date_range(start=start, end=end, freq='h')
    np.random.seed(42)
    df = pd.DataFrame({"Timestamp": date_range})
    
    if "Prices" in metric or "Index" in metric:
        base = 8.0 + 3.0 * np.sin(df.index / 24 * 2 * np.pi)
        df[metric] = base + np.random.normal(0, 1.5, len(df))
    elif "Traffic" in metric:
        df[metric] = np.random.choice([1, 2, 3], size=len(df), p=[0.85, 0.12, 0.03])
    elif "Solar" in metric:
        hour_of_day = df["Timestamp"].dt.hour
        df[metric] = 5000 * np.maximum(0, np.sin((hour_of_day - 6) / 12 * np.pi)) * np.random.uniform(0.7, 1.1, len(df))
    elif "Wind" in metric:
        df[metric] = (8000 + 4000 * np.sin(df.index / 100) + np.random.normal(0, 500, len(df))).clip(0)
    elif "Balance" in metric or "aFRR" in metric:
        df[metric] = np.random.normal(0, 400, len(df))
    elif "Redispatch" in metric or "Reserve" in metric:
        df[metric] = np.random.choice([0, 150, 300], size=len(df), p=[0.90, 0.08, 0.02])
    else:
        df[metric] = np.random.uniform(10, 100, len(df))
        
    return df

# --- Main Logic: Fetching & Merging Curve Data ---
df_master = pd.DataFrame()

if not selected_metrics:
    st.info("👈 Please select some parameters in the sidebar to begin analyzing curves.")
else:
    series_to_merge = []
    
    if use_demo:
        logs.append("Demo Mode active. Compiling mock data...")
        for metric in selected_metrics:
            df_m = generate_mock_data(metric, date_from, date_to)
            series_to_merge.append(df_m[["Timestamp", metric]])
    else:
        if not client_id or not client_secret:
            st.warning("Please enter your Client ID and Client Secret in the sidebar.")
        else:
            token, auth_err = fetch_token(client_id, client_secret)
            if auth_err:
                st.error(f"Authentication Error: {auth_err}")
            elif token:
                for metric in selected_metrics:
                    meta = endpoint_options[metric]
                    logs.append(f"Requesting '{metric}'...")
                    parsed_df, fetch_err = fetch_single_metric(
                        meta["data"], 
                        meta["product"], 
                        date_from, 
                        date_to, 
                        token
                    )
                    
                    if fetch_err:
                        st.error(f"Failed to load '{metric}': {fetch_err}")
                    elif parsed_df is not None and not parsed_df.empty:
                        # Ensure we successfully established a timeline
                        if "Timestamp" not in parsed_df.columns:
                            st.warning(f"⚠️ Could not align '{metric}' - No timeline identified in API response.")
                            continue
                            
                        # Find value columns
                        val_cols = [c for c in parsed_df.columns if c not in ["Timestamp", "Datum", "von", "Zeitzone von", "bis", "Zeitzone bis", "Status Label"]]
                        if val_cols:
                            primary_col = val_cols[0]
                            
                            # Convert non-numeric categorical data (like Green, Yellow, Red grid statuses)
                            if parsed_df[primary_col].dtype == object:
                                parsed_df[primary_col] = parsed_df[primary_col].astype(str).str.strip()
                                # Handle traffic light categories directly
                                parsed_df[primary_col] = parsed_df[primary_col].replace({
                                    "Grün": 1, "Green": 1, "GRÜN": 1,
                                    "Gelb": 2, "Yellow": 2, "GELB": 2,
                                    "Rot": 3, "Red": 3, "ROT": 3
                                }, regex=True)
                                
                            parsed_df[primary_col] = pd.to_numeric(parsed_df[primary_col], errors='coerce')
                            parsed_df = parsed_df.rename(columns={primary_col: metric})
                            
                            series_to_merge.append(parsed_df[["Timestamp", metric]])
                            logs.append(f"Successfully processed series '{metric}'")
                        else:
                            logs.append(f"No valid numeric or status columns identified for series '{metric}'")

    # Outer join all retrieved series on their Timestamp
    if series_to_merge:
        df_master = series_to_merge[0]
        for df_next in series_to_merge[1:]:
            df_master = pd.merge(df_master, df_next, on="Timestamp", how="outer")
        
        df_master = df_master.sort_values("Timestamp").reset_index(drop=True)

# --- Visualization Render Section ---
if not df_master.empty:
    st.subheader("Interactive Grid Trends")
    
    # Static Date Selectors for window zooming
    st.write("🔍 **Zoom Window**")
    col_start, col_end = st.columns(2)
    
    min_date = df_master["Timestamp"].min().date()
    max_date = df_master["Timestamp"].max().date()
    
    with col_start:
        zoom_start = st.date_input("Zoom Start Date", value=min_date, min_value=min_date, max_value=max_date)
    with col_end:
        zoom_end = st.date_input("Zoom End Date", value=max_date, min_value=min_date, max_value=max_date)
    
    # Convert date inputs back to datetime objects for accurate filtering
    zoom_start_dt = datetime.datetime.combine(zoom_start, datetime.time.min)
    zoom_end_dt = datetime.datetime.combine(zoom_end, datetime.time.max)
    
    # Filter dataset based on inputs
    df_filtered = df_master[
        (df_master["Timestamp"] >= zoom_start_dt) & 
        (df_master["Timestamp"] <= zoom_end_dt)
    ]
    
    # Generate interactive multi-line plot using Plotly
    metric_cols = [c for c in df_filtered.columns if c != "Timestamp"]
    
    fig = px.line(
        df_filtered, 
        x="Timestamp", 
        y=metric_cols,
        title="Comparative Grid Data (Double-click legend items to isolate curves)"
    )
    
    fig.update_layout(
        hovermode="x unified", 
        yaxis_title="Metric Values (Units vary by metric)",
        legend_title_text="Visible Curves"
    )
    st.plotly_chart(fig, use_container_width=True)
    
    # Display joint datatable
    with st.expander("Show Unified Dataset Table"):
        st.dataframe(df_filtered)
        
else:
    if not use_demo and client_id and client_secret:
        st.info("No matching series could be merged. Please check credentials or verify the diagnostics below.")

# --- Diagnostic System Logs ---
st.write("---")
with st.expander("🛠️ Diagnostics and Server Trace", expanded=True):
    for log in logs:
        st.text(log)
