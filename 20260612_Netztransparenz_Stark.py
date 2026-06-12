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

# --- Sidebar: Authentication & Configuration ---
st.sidebar.header("API Configuration")

# Toggle to use simulated data for testing/demo purposes
use_mock_data = st.sidebar.checkbox("Use Simulated Data (Demo Mode)", value=True)

# Input fields for credentials (fallback to environment variables)
client_id_env = os.environ.get('IPNT_CLIENT_ID', '')
client_secret_env = os.environ.get('IPNT_CLIENT_SECRET', '')

client_id = st.sidebar.text_input("Client ID", value=client_id_env, type="password")
client_secret = st.sidebar.text_input("Client Secret", value=client_secret_env, type="password")


# Cache the token to avoid requesting a new one on every user interaction
@st.cache_data(ttl=3500)  # OAuth2 tokens typically last 1 hour (3600 seconds)
def get_access_token(cid, secret):
    if not cid or not secret:
        return None
    url = "https://identity.netztransparenz.de/users/connect/token"
    payload = {
        'grant_type': 'client_credentials',
        'client_id': cid,
        'client_secret': secret
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.ok:
            return response.json().get('access_token')
        else:
            st.sidebar.error(f"Auth Error: {response.status_code} - {response.reason}")
            return None
    except Exception as e:
        st.sidebar.error(f"Connection failed: {str(e)}")
        return None


token = None
if not use_mock_data:
    if client_id and client_secret:
        token = get_access_token(client_id, client_secret)
        if token:
            st.sidebar.success("Successfully authenticated with API")
    else:
        st.sidebar.warning("Please enter your Client ID and Secret, or use Demo Mode.")

# --- Sidebar: Query Parameters ---
st.sidebar.header("Filter & Query Parameters")

# Mapping of business cases to specific endpoints
endpoint_options = {
    "Spot Market Prices (EUR/MWh)": {"data": "Spotmarktpreise", "product": "none"},
    "Grid Traffic Light Status": {"data": "TrafficLight", "product": "none"},
    "Solar Online Generation Forecast": {"data": "onlineHochrechnung", "product": "Solar"},
    "Wind Onshore Online Forecast": {"data": "onlineHochrechnung", "product": "Windonshore"},
    "Redispatch Volumes": {"data": "redispatch", "product": "none"},
}

selected_metric = st.sidebar.selectbox("Select Grid Metric", list(endpoint_options.keys()))
endpoint_meta = endpoint_options[selected_metric]

# Date selection (Netztransparenz API usually expects YYYY-MM-DD)
today = datetime.date.today()
date_from = st.sidebar.date_input("Start Date", today - datetime.timedelta(days=7))
date_to = st.sidebar.date_input("End Date", today)

if date_from > date_to:
    st.error("Error: Start Date must be before or equal to End Date.")


# --- Data Retrieval & Simulation Layer ---
def fetch_netztransparenz_data(data_param, product_param, start, end, auth_token):
    # Format dates as string YYYY-MM-DD
    str_start = start.strftime("%Y-%m-%d")
    str_end = end.strftime("%Y-%m-%d")

    url = f"https://ds.netztransparenz.de/api/v1/data/{data_param}/{product_param}/{str_start}/{str_end}"

    headers = {'Authorization': f'Bearer {auth_token}'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.ok:
            # Assuming API returns JSON with an array of values and timestamps
            # Adjust normalization logic based on actual API payload format
            data = response.json()
            df = pd.DataFrame(data)
            return df
        else:
            st.error(f"API Error {response.status_code}: {response.reason}")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Failed to fetch data: {str(e)}")
        return pd.DataFrame()


def generate_mock_data(metric, start, end):
    """Generates synthetic data matching the expected shape of the chosen metric."""
    date_range = pd.date_range(start=start, end=end, freq='H')
    np.random.seed(42)  # Set seed for consistency

    df = pd.DataFrame({"Timestamp": date_range})

    if "Prices" in metric:
        # Base price fluctuated around a mean, with occasional high or negative spikes
        base = 80 + 30 * np.sin(df.index / 24 * 2 * np.pi)
        noise = np.random.normal(0, 15, len(df))
        df["Value (EUR/MWh)"] = base + noise
    elif "Traffic" in metric:
        # Green (1), Yellow (2), Red (3) status representation
        df["Status Value"] = np.random.choice([1, 2, 3], size=len(df), p=[0.85, 0.12, 0.03])
        df["Status Label"] = df["Status Value"].map({1: "Green (Normal)", 2: "Yellow (Warning)", 3: "Red (Congestion)"})
    elif "Solar" in metric:
        # Bell curve repeating daily
        hour_of_day = df["Timestamp"].dt.hour
        df["Generation (MW)"] = 5000 * np.maximum(0, np.sin((hour_of_day - 6) / 12 * np.pi)) * np.random.uniform(0.7,
                                                                                                                 1.1,
                                                                                                                 len(df))
    elif "Wind" in metric:
        # Slow moving weather patterns
        df["Generation (MW)"] = 8000 + 4000 * np.sin(df.index / 100) + np.random.normal(0, 500, len(df))
        df["Generation (MW)"] = df["Generation (MW)"].clip(lower=0)
    else:
        # General backup
        df["Value"] = np.random.uniform(10, 100, len(df))

    return df


# --- UI Execution and Visualizations ---

if use_mock_data:
    st.info(
        "ℹ️ Running in **Demo Mode** with simulated data. Uncheck the box in the sidebar to try a live API connection.")
    df_data = generate_mock_data(selected_metric, date_from, date_to)
else:
    if not token:
        st.warning("Please authenticate by adding your credentials in the sidebar to fetch live data.")
        df_data = pd.DataFrame()
    else:
        with st.spinner("Fetching data from Netztransparenz API..."):
            df_data = fetch_netztransparenz_data(
                endpoint_meta["data"],
                endpoint_meta["product"],
                date_from,
                date_to,
                token
            )

# Render Data if available
if not df_data.empty:
    st.subheader(f"Data analysis for: {selected_metric}")

    # Establish dynamic column names for display based on metric type
    value_col = [col for col in df_data.columns if col not in ["Timestamp", "Status Label"]][0]

    # Grid metrics summary cards
    col1, col2, col3 = st.columns(3)

    if "Status Label" in df_data.columns:
        red_incidents = len(df_data[df_data["Status Value"] == 3])
        yellow_incidents = len(df_data[df_data["Status Value"] == 2])
        col1.metric("Red Phases Count", f"{red_incidents} hours")
        col2.metric("Yellow Phases Count", f"{yellow_incidents} hours")
        col3.metric("Normal/Green Share", f"{(len(df_data) - red_incidents - yellow_incidents) / len(df_data):.1%}")
    else:
        col1.metric("Maximum Value", f"{df_data[value_col].max():.2f}")
        col2.metric("Minimum Value", f"{df_data[value_col].min():.2f}")
        col3.metric("Average Value", f"{df_data[value_col].mean():.2f}")

    # Plotting Data
    if "Status Label" in df_data.columns:
        # Categorical plot for traffic lights
        fig = px.scatter(
            df_data,
            x="Timestamp",
            y="Status Label",
            color="Status Label",
            color_discrete_map={"Green (Normal)": "green", "Yellow (Warning)": "orange", "Red (Congestion)": "red"},
            title="Grid Traffic Light Timeline"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        # Time-series plot for numeric data
        fig = px.line(
            df_data,
            x="Timestamp" if "Timestamp" in df_data.columns else df_data.index,
            y=value_col,
            title=f"{selected_metric} Over Time"
        )
        # Highlight negative zones if looking at market prices
        if "Prices" in selected_metric:
            fig.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Negative Price Threshold")

        st.plotly_chart(fig, use_container_width=True)

    # Show raw data option
    with st.expander("Show Raw Data Table"):
        st.dataframe(df_data)
else:
    if not use_mock_data and token:
        st.warning(
            "No data returned from the API for the selected timeframe. Verify if the API endpoint supports the chosen date range.")