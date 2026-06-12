import os
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

st.title("🔌 Electricity Grid & Charging Network Analyzer")
st.write(
    "This dashboard monitors key grid metrics from Netztransparenz.de to assist "
    "with charging station network planning and smart-charging optimization."
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

st.sidebar.header("2. Parameters")
endpoint_options = {
    "Spot Market Prices": {"data": "Spotmarktpreise", "product": "none"},
    "Grid Traffic Light": {"data": "TrafficLight", "product": "none"},
    "Solar Forecast": {"data": "onlineHochrechnung", "product": "Solar"},
    "Wind Onshore Forecast": {"data": "onlineHochrechnung", "product": "Windonshore"},
}
selected_metric = st.sidebar.selectbox("Metric", list(endpoint_options.keys()))
endpoint_meta = endpoint_options[selected_metric]

# Date selection (Defaults to past dates to ensure historical data exists)
today = datetime.date.today()
date_from = st.sidebar.date_input("Start Date", today - datetime.timedelta(days=7))
date_to = st.sidebar.date_input("End Date", today - datetime.timedelta(days=3))

if date_to > today:
    st.sidebar.warning("⚠️ Selected dates include future dates. Real-time historical grid data might not exist yet.")

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

# --- Robust Data Fetching Function with Fallback Routing ---
def fetch_api_data(data_param, product_param, start, end, token):
    str_start = start.strftime("%Y-%m-%d")
    str_end = end.strftime("%Y-%m-%d")
    headers = {'Authorization': f'Bearer {token}'}
    
    # Define potential URL variations for endpoints with "n/e" (not applicable) products
    urls_to_try = []
    
    if product_param == "none":
        # Pattern 1: Omit the product segment entirely (Common for 'n/e' endpoints)
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/{str_start}/{str_end}")
        # Pattern 2: Use a hyphen placeholder
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/-/{str_start}/{str_end}")
        # Pattern 3: Use 'none' as a literal string
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/none/{str_start}/{str_end}")
    else:
        # Standard pattern with the active product segment
        urls_to_try.append(f"https://ds.netztransparenz.de/api/v1/data/{data_param}/{product_param}/{str_start}/{str_end}")
        
    last_err = None
    for url in urls_to_try:
        logs.append(f"Attempting API request: {url}")
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.ok:
                logs.append(f"Successful connection with URL: {url}")
                raw_json = res.json()
                return raw_json, None
            else:
                last_err = f"Failed (Code {res.status_code}): {res.reason} | Response: {res.text}"
                logs.append(f"Attempt failed: {last_err}")
        except Exception as e:
            last_err = f"Connection error: {str(e)}"
            logs.append(f"Attempt failed with exception: {last_err}")
            
    return None, f"All routing configurations failed. Details: {last_err}"

# --- Mock Data Generation ---
def generate_mock_data(metric, start, end):
    date_range = pd.date_range(start=start, end=end, freq='H')
    np.random.seed(42)
    df = pd.DataFrame({"Timestamp": date_range})
    
    if "Prices" in metric:
        base = 80 + 30 * np.sin(df.index / 24 * 2 * np.pi)
        noise = np.random.normal(0, 15, len(df))
        df["Value (EUR/MWh)"] = base + noise
    elif "Traffic" in metric:
        df["Status Value"] = np.random.choice([1, 2, 3], size=len(df), p=[0.85, 0.12, 0.03])
        df["Status Label"] = df["Status Value"].map({1: "Green (Normal)", 2: "Yellow (Warning)", 3: "Red (Congestion)"})
    elif "Solar" in metric:
        hour_of_day = df["Timestamp"].dt.hour
        df["Generation (MW)"] = 5000 * np.maximum(0, np.sin((hour_of_day - 6) / 12 * np.pi)) * np.random.uniform(0.7, 1.1, len(df))
    elif "Wind" in metric:
        df["Generation (MW)"] = 8000 + 4000 * np.sin(df.index / 100) + np.random.normal(0, 500, len(df))
        df["Generation (MW)"] = df["Generation (MW)"].clip(lower=0)
    else:
        df["Value"] = np.random.uniform(10, 100, len(df))
        
    return df

# --- Main Logic execution ---
df_data = pd.DataFrame()
error_message = None

if use_demo:
    logs.append("Demo Mode active. Displaying simulated data...")
    df_data = generate_mock_data(selected_metric, date_from, date_to)
else:
    if not client_id or not client_secret:
        error_message = "Please enter both Client ID and Client Secret in the sidebar."
        logs.append("Execution halted: Missing credentials.")
    else:
        logs.append("Retrieving Access Token...")
        token, auth_err = fetch_token(client_id, client_secret)
        if auth_err:
            error_message = f"Authentication Error: {auth_err}"
            logs.append(error_message)
        elif token:
            logs.append("Access Token acquired. Initializing data pull...")
            raw_data, fetch_err = fetch_api_data(
                endpoint_meta["data"], 
                endpoint_meta["product"], 
                date_from, 
                date_to, 
                token
            )
            if fetch_err:
                error_message = f"Data Pull Error: {fetch_err}"
            elif raw_data:
                try:
                    # Parse the structure dynamically
                    if isinstance(raw_data, list):
                        df_data = pd.DataFrame(raw_data)
                    elif isinstance(raw_data, dict):
                        # Search for typical payload list keys
                        for key in ["data", "values", "responseData"]:
                            if key in raw_data and isinstance(raw_data[key], list):
                                df_data = pd.DataFrame(raw_data[key])
                                break
                        if df_data.empty:
                            df_data = pd.DataFrame([raw_data])
                    
                    logs.append(f"Successfully constructed DataFrame. Columns found: {list(df_data.columns)}")
                except Exception as parse_ex:
                    error_message = f"JSON parsing failed: {str(parse_ex)}"
                    logs.append(error_message)

# --- Visual Render Section ---
if error_message:
    st.error(error_message)

if not df_data.empty:
    st.subheader(f"Analysis Window: {selected_metric}")
    
    # Identify value and timeline columns dynamically
    cols = list(df_data.columns)
    x_col = "Timestamp" if "Timestamp" in cols else (cols[0] if len(cols) > 0 else None)
    
    # Pick the first column that isn't the timestamp to represent the value
    remaining_cols = [c for c in cols if c != x_col and c != "Status Label"]
    y_col = remaining_cols[0] if remaining_cols else None
    
    # Display statistics cards
    col1, col2, col3 = st.columns(3)
    if "Status Label" in cols:
        reds = len(df_data[df_data["Status Value"] == 3])
        yellows = len(df_data[df_data["Status Value"] == 2])
        col1.metric("Red (Congested) Hours", f"{reds} hrs")
        col2.metric("Yellow (Warning) Hours", f"{yellows} hrs")
        col3.metric("Normal Grid State Share", f"{(len(df_data) - reds - yellows)/len(df_data):.1%}")
    elif y_col:
        try:
            numeric_vals = pd.to_numeric(df_data[y_col], errors='coerce')
            col1.metric("Maximum Value", f"{numeric_vals.max():.2f}")
            col2.metric("Minimum Value", f"{numeric_vals.min():.2f}")
            col3.metric("Average Value", f"{numeric_vals.mean():.2f}")
        except Exception:
            pass

    # Plot chart
    if "Status Label" in cols:
        fig = px.scatter(
            df_data, 
            x=x_col, 
            y="Status Label", 
            color="Status Label",
            color_discrete_map={"Green (Normal)": "green", "Yellow (Warning)": "orange", "Red (Congestion)": "red"},
            title="Grid State Over Time"
        )
        st.plotly_chart(fig, use_container_width=True)
    elif x_col and y_col:
        try:
            fig = px.line(df_data, x=x_col, y=y_col, title=f"Trend line: {selected_metric}")
            if "Prices" in selected_metric:
                fig.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Negative Price Level")
            st.plotly_chart(fig, use_container_width=True)
        except Exception as plot_err:
            st.warning(f"Could not automatically map chart parameters: {str(plot_err)}")
            
    st.write("Data Table")
    st.dataframe(df_data)
else:
    if not error_message:
        st.info("No active dataset to show. Verify parameters or see diagnostic logs below.")

# --- Diagnostic System Logs ---
st.write("---")
with st.expander("🛠️ Diagnostics and Server Trace", expanded=True):
    for log in logs:
        st.text(log)
