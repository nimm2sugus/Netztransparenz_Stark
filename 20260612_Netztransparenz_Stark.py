import os
import requests
import datetime
import pandas as pd
import numpy as np
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Grid Analyzer", layout="wide")

st.title("🔌 Electricity Grid & Charging Network Analyzer")

# --- Sidebar: Configuration ---
st.sidebar.header("1. Connection Settings")
use_demo = st.sidebar.checkbox("Use Demo Mode (Simulated Data)", value=True)

# Credentials inputs
client_id = st.sidebar.text_input("Client ID", value=os.environ.get('IPNT_CLIENT_ID', ''), type="password")
client_secret = st.sidebar.text_input("Client Secret", value=os.environ.get('IPNT_CLIENT_SECRET', ''), type="password")

st.sidebar.header("2. Parameters")
endpoint_options = {
    "Spot Market Prices": {"data": "Spotmarktpreise", "product": "none"},
    "Grid Traffic Light": {"data": "TrafficLight", "product": "none"},
    "Solar Forecast": {"data": "onlineHochrechnung", "product": "Solar"},
    "Wind Onshore Forecast": {"data": "onlineHochrechnung", "product": "Windonshore"},
}
selected_metric = st.sidebar.selectbox("Metric", list(endpoint_options.keys()))
endpoint_meta = endpoint_options[selected_metric]

# Date selection
today = datetime.date.today()
date_from = st.sidebar.date_input("Start Date", today - datetime.timedelta(days=3))
date_to = st.sidebar.date_input("End Date", today)

# Track logs to display at the bottom for troubleshooting
logs = []


# --- Authentication Function ---
def fetch_token(cid, secret):
    url = "https://identity.netztransparenz.de/users/connect/token"
    payload = {
        'grant_type': 'client_credentials',
        'client_id': cid,
        'client_secret': secret
    }
    try:
        logs.append(f"Attempting authentication at: {url}")
        res = requests.post(url, data=payload, timeout=10)
        if res.ok:
            logs.append("Authentication successful.")
            return res.json().get('access_token'), None
        else:
            err = f"Auth failed. Code: {res.status_code}, Reason: {res.reason}, Response: {res.text}"
            logs.append(err)
            return None, err
    except Exception as e:
        err = f"Auth connection error: {str(e)}"
        logs.append(err)
        return None, err


# --- Data Fetching Function ---
def fetch_api_data(data_param, product_param, start, end, token):
    str_start = start.strftime("%Y-%m-%d")
    str_end = end.strftime("%Y-%m-%d")

    # URL structure as per documentation
    url = f"https://ds.netztransparenz.de/api/v1/data/{data_param}/{product_param}/{str_start}/{str_end}"
    logs.append(f"Requesting URL: {url}")

    headers = {'Authorization': f'Bearer {token}'}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.ok:
            logs.append("Data retrieval successful.")
            raw_json = res.json()
            # Log a small preview of the incoming data structure
            logs.append(f"Raw API Response preview: {str(raw_json)[:500]}...")
            return raw_json, None
        else:
            err = f"Data fetch failed. Code: {res.status_code}, Reason: {res.reason}, Response: {res.text}"
            logs.append(err)
            return None, err
    except Exception as e:
        err = f"Request connection error: {str(e)}"
        logs.append(err)
        return None, err


# --- Main Logic ---
df_data = pd.DataFrame()
error_message = None

if use_demo:
    logs.append("Running in Demo Mode. Generating synthetic data...")
    # Generate simple mock data
    date_range = pd.date_range(start=date_from, end=date_to, freq='H')
    np.random.seed(42)
    df_data = pd.DataFrame({
        "Timestamp": date_range,
        "Value": 50 + 20 * np.sin(np.arange(len(date_range)) / 12) + np.random.normal(0, 5, len(date_range))
    })
else:
    if not client_id or not client_secret:
        error_message = "Please enter both Client ID and Client Secret in the sidebar to fetch live data."
        logs.append("Retrieval aborted: Missing credentials.")
    else:
        token, auth_err = fetch_token(client_id, client_secret)
        if auth_err:
            error_message = f"Authentication Error: {auth_err}"
        elif token:
            raw_data, fetch_err = fetch_api_data(
                endpoint_meta["data"],
                endpoint_meta["product"],
                date_from,
                date_to,
                token
            )
            if fetch_err:
                error_message = f"Data Fetch Error: {fetch_err}"
            elif raw_data:
                try:
                    # Defensive parsing of the JSON structure
                    if isinstance(raw_data, list):
                        df_data = pd.DataFrame(raw_data)
                    elif isinstance(raw_data, dict):
                        # If nested under a key like "data" or "values"
                        for key in ["data", "values", "responseData"]:
                            if key in raw_data:
                                df_data = pd.DataFrame(raw_data[key])
                                break
                        if df_data.empty:
                            df_data = pd.DataFrame([raw_data])

                    logs.append(f"Parsed DataFrame columns: {list(df_data.columns)}")
                except Exception as parse_ex:
                    error_message = f"Failed to parse JSON into table: {str(parse_ex)}"
                    logs.append(error_message)

# --- Render UI Elements ---
if error_message:
    st.error(error_message)

if not df_data.empty:
    st.subheader(f"Visualization: {selected_metric}")

    # Try to identify timestamp and value columns dynamically
    cols = list(df_data.columns)
    x_col = "Timestamp" if "Timestamp" in cols else (cols[0] if len(cols) > 0 else None)
    y_col = "Value" if "Value" in cols else (cols[1] if len(cols) > 1 else cols[0])

    try:
        fig = px.line(df_data, x=x_col, y=y_col, title=f"{selected_metric} Trend")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_data)
    except Exception as chart_ex:
        st.warning(f"Could not render chart automatically: {str(chart_ex)}")
        st.write("Raw data table:")
        st.dataframe(df_data)
else:
    if not error_message:
        st.info("No data available to display. Please check the logs below.")

# --- System Logs for Troubleshooting ---
st.write("---")
with st.expander("🛠️ System Logs & Debugging Info", expanded=True):
    for log in logs:
        st.text(log)
