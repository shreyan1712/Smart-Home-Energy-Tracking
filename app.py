import datetime
import streamlit as st
import pandas as pd
import os
import shutil
import platform

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

# Supabase Connection check
try:
    from supabase import create_client, Client
    SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
    SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
    if SUPABASE_URL and SUPABASE_KEY:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    else:
        supabase = None
except Exception:
    supabase = None

st.set_page_config(page_title="Smart Home Electricity Usage and Tariff Monitor", page_icon="⚡", layout="wide")

METER_INFO = ("MTR1001", "single-phase")
DAILY_LIMIT = 10

# Session state initialization for Auth
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None

if "local_users" not in st.session_state:
    st.session_state.local_users = {}

if "local_data" not in st.session_state:
    st.session_state.local_data = {
        "appliances": [],
        "logs": [],
        "bills": {},
        "meter_readings": [],
        "budget": 500.0
    }


class InvalidReadingError(Exception):
    pass


# --- APPLIANCE CLASSES ---
class Appliance:
    def __init__(self, app_id, name, watts, category="general"):
        self.app_id = app_id
        self.name = name
        self.watts = watts
        self.category = category

    def calculate_energy_units(self, hours):
        return (self.watts * hours) / 1000


class HeatingAppliance(Appliance):
    def __init__(self, app_id, name, watts):
        super().__init__(app_id, name, watts, "heating")

    def calculate_energy_units(self, hours):
        return (self.watts * hours) / 1000


class CoolingAppliance(Appliance):
    def __init__(self, app_id, name, watts):
        super().__init__(app_id, name, watts, "cooling")

    def calculate_energy_units(self, hours):
        return (self.watts * hours * 0.8) / 1000


class EntertainmentAppliance(Appliance):
    def __init__(self, app_id, name, watts):
        super().__init__(app_id, name, watts, "entertainment")

    def calculate_energy_units(self, hours):
        running = (self.watts * hours) / 1000
        standby = (5 * (24 - hours)) / 1000
        return running + standby


def create_appliance_obj(app_id, name, category, watts):
    if category == "heating":
        return HeatingAppliance(app_id, name, watts)
    elif category == "cooling":
        return CoolingAppliance(app_id, name, watts)
    else:
        return EntertainmentAppliance(app_id, name, watts)


def calculate_bill(units):
    if units <= 100:
        bill = units * 3
    elif units <= 200:
        bill = 100 * 3 + (units - 100) * 5
    else:
        bill = 100 * 3 + 100 * 5 + (units - 200) * 7
    return bill


# --- LOGIN / SIGNUP PORTAL ---
def login_signup_page():
    st.title("⚡ Smart Home Electricity Usage and Tariff Monitor")
    st.markdown("### Secure Login / Signup Portal")
    
    tab1, tab2 = st.tabs(["🔐 Login", "📝 Sign Up"])
    
    with tab1:
        st.subheader("Login to your account")
        username = st.text_input("Username", key="login_user").strip()
        password = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login"):
            if not username or not password:
                st.error("Please enter both username and password.")
            else:
                if supabase:
                    try:
                        res = supabase.table("users").select("*").eq("username", username).eq("password", password).execute()
                        if res.data:
                            st.session_state.logged_in = True
                            st.session_state.current_user = username
                            st.success("Login successful!")
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")
                    except Exception as e:
                        st.error(f"Database error: {e}")
                else:
                    if username in st.session_state.local_users and st.session_state.local_users[username] == password:
                        st.session_state.logged_in = True
                        st.session_state.current_user = username
                        st.success("Login successful!")
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")
                        
    with tab2:
        st.subheader("Create a new account")
        new_user = st.text_input("Choose Username", key="signup_user").strip()
        new_pass = st.text_input("Choose Password", type="password", key="signup_pass")
        if st.button("Sign Up"):
            if not new_user or not new_pass:
                st.error("Please fill in all fields.")
            else:
                if supabase:
                    try:
                        existing = supabase.table("users").select("*").eq("username", new_user).execute()
                        if existing.data:
                            st.error("Username already exists. Choose another.")
                        else:
                            supabase.table("users").insert({"username": new_user, "password": new_pass}).execute()
                            st.success("Account created successfully! Please go to Login tab.")
                    except Exception as e:
                        st.error(f"Error creating user: {e}")
                else:
                    if new_user in st.session_state.local_users:
                        st.error("Username already exists.")
                    else:
                        st.session_state.local_users[new_user] = new_pass
                        st.success("Account created successfully! Please go to Login tab.")


# --- MAIN APP LOGIC ---
def main_app():
    user = st.session_state.current_user
    st.sidebar.title(f"Welcome, {user}! 👋")
    
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.current_user = None
        st.rerun()

    menu = st.sidebar.selectbox("Main Menu", [
        "1. Add appliance",
        "2. Show appliances",
        "3. Log daily usage",
        "4. Show monthly bill",
        "5. Show all usage logs",
        "6. Enter meter reading",
        "7. Usage analysis",
        "8. Graphs",
        "9. Set monthly target budget",
        "10. Data management & Reset",
        "11. Open project folder"
    ])

    # Fetch Data from Supabase or Local State
    appliances_list = []
    usage_logs = []
    monthly_summary = {}
    meter_readings = []
    target_budget = 500.0

    if supabase:
        try:
            apps_res = supabase.table("appliances").select("*").eq("username", user).execute()
            appliances_list = apps_res.data
            
            logs_res = supabase.table("usage_logs").select("*").eq("username", user).execute()
            usage_logs = logs_res.data
            
            bills_res = supabase.table("monthly_summary").select("*").eq("username", user).execute()
            for b in bills_res.data:
                monthly_summary[b["month"]] = {"units": b["units"], "bill": b["bill"]}
                
            meter_res = supabase.table("meter_readings").select("*").eq("username", user).execute()
            meter_readings = [m["reading"] for m in meter_res.data]
            
            budget_res = supabase.table("budget").select("*").eq("username", user).execute()
            if budget_res.data:
                target_budget = budget_res.data[0]["target_budget"]
        except Exception as e:
            st.sidebar.error(f"Data sync error: {e}")
    else:
        d = st.session_state.local_data
        appliances_list = [a for a in d["appliances"] if a.get("username") == user]
        usage_logs = [l for l in d["logs"] if l.get("username") == user]
        monthly_summary = d["bills"]
        meter_readings = d["meter_readings"]
        target_budget = d["budget"]

    # Helper maps
    appliances_dict = {a["app_id"]: create_appliance_obj(a["app_id"], a["name"], a["category"], a["watts"]) for a in appliances_list}
    active_devices = set(appliances_dict.keys())

    # --- 1. Add Appliance ---
    if menu == "1. Add appliance":
        st.header("🔌 Add Appliance")
        name = st.text_input("Appliance name").strip()
        category = st.selectbox("Type", ["heating", "cooling", "entertainment"])
        watts = st.number_input("Power rating in watts", min_value=1.0, value=100.0)

        if st.button("Save Appliance"):
            if not name:
                st.error("Name cannot be empty.")
            else:
                app_id = f"A{len(appliances_list) + 1}"
                if supabase:
                    try:
                        supabase.table("appliances").insert({
                            "username": user, "app_id": app_id, "name": name, "category": category, "watts": watts
                        }).execute()
                        st.success(f"Appliance added with ID {app_id}")
                    except Exception as e:
                        st.error(f"Error saving appliance: {e}")
                else:
                    st.session_state.local_data["appliances"].append({
                        "username": user, "app_id": app_id, "name": name, "category": category, "watts": watts
                    })
                    st.success(f"Appliance added with ID {app_id}")

    # --- 2. Show Appliances ---
    elif menu == "2. Show appliances":
        st.header(f"📋 Appliances (Meter: {METER_INFO[0]} | {METER_INFO[1]})")
        if not appliances_list:
            st.info("No appliance added yet.")
        else:
            df = pd.DataFrame(appliances_list)
            st.dataframe(df[["app_id", "name", "category", "watts"]], use_container_width=True)

    # --- 3. Log Daily Usage ---
    elif menu == "3. Log daily usage":
        st.header("📝 Log Daily Usage")
        if not appliances_list:
            st.warning("Please add an appliance first.")
        else:
            app_choice = st.selectbox("Select Appliance", [f"{a['app_id']} - {a['name']} ({a['category']})" for a in appliances_list])
            selected_app_id = app_choice.split(" - ")[0]
            selected_app_data = next(a for a in appliances_list if a["app_id"] == selected_app_id)
            app_obj = create_appliance_obj(selected_app_data["app_id"], selected_app_data["name"], selected_app_data["category"], selected_app_data["watts"])

            date_input = st.date_input("Date", datetime.date.today())
            hour = st.slider("At which hour did you use it (0-23)?", 0, 23, 12)
            hours_used = st.number_input("For how many hours was it used?", min_value=0.1, max_value=24.0, value=2.0)

            if st.button("Save Usage Log"):
                units = round(app_obj.calculate_energy_units(hours_used), 3)
                month_key = str(date_input)[:7]

                if supabase:
                    try:
                        supabase.table("usage_logs").insert({
                            "username": user, "date": str(date_input), "hour": hour,
                            "app_id": app_obj.app_id, "name": app_obj.name,
                            "hours": hours_used, "units": units
                        }).execute()

                        # Update monthly summary in database
                        m_units = sum(l["units"] for l in usage_logs if l["date"].startswith(month_key)) + units
                        m_bill = calculate_bill(m_units)
                        supabase.table("monthly_summary").upsert({
                            "username": user, "month": month_key, "units": round(m_units, 2), "bill": round(m_bill, 2)
                        }).execute()
                    except Exception as e:
                        st.error(f"Error logging usage: {e}")
                else:
                    st.session_state.local_data["logs"].append({
                        "username": user, "date": str(date_input), "hour": hour,
                        "app_id": app_obj.app_id, "name": app_obj.name,
                        "hours": hours_used, "units": units
                    })

                st.success(f"Saved: {app_obj.name} used {units} kWh on {date_input}")

                # Daily total check
                day_total = sum(l["units"] for l in usage_logs if l["date"] == str(date_input)) + units
                st.info(f"Total usage on {date_input} = {round(day_total, 2)} kWh")
                if day_total > DAILY_LIMIT:
                    st.warning(f"⚠️ ALERT: High consumption! Usage is more than {DAILY_LIMIT} kWh in a day.")

    # --- 4. Show Monthly Bill ---
    elif menu == "4. Show monthly bill":
        st.header("💰 Monthly Bill")
        month = st.text_input("Enter month (YYYY-MM)", str(datetime.date.today())[:7])
        
        if usage_logs:
            month_units = sum(l["units"] for l in usage_logs if l["date"].startswith(month))
            bill = calculate_bill(month_units)
            
            st.metric("Month", month)
            st.metric("Units Used", f"{round(month_units, 2)} kWh")
            st.metric("Estimated Bill", f"Rs {round(bill, 2)}")
            
            if month_units > 200:
                st.info("Slab: Above 200 units (Rs 7 per unit)")
            elif month_units > 100:
                st.info("Slab: 101 to 200 units (Rs 5 per unit)")
            else:
                st.info("Slab: First 100 units (Rs 3 per unit)")
        else:
            st.info(f"No usage found for {month}")

    # --- 5. Show All Usage Logs ---
    elif menu == "5. Show all usage logs":
        st.header("📊 All Usage Logs")
        if not usage_logs:
            st.info("No logs yet.")
        else:
            df = pd.DataFrame(usage_logs)
            st.dataframe(df[["date", "hour", "app_id", "name", "hours", "units"]], use_container_width=True)

    # --- 6. Enter Meter Reading ---
    elif menu == "6. Enter meter reading":
        st.header("🔢 Meter Reading")
        reading_date = st.date_input("Reading Date", datetime.date.today(), key="m_read_date")
        reading = st.number_input("Enter meter reading (kWh)", min_value=0.0)

        if st.button("Save Meter Reading"):
            if meter_readings and reading < meter_readings[-1]:
                st.error(f"Reading is less than the last reading ({meter_readings[-1]}). Meter cannot go back.")
            else:
                if supabase:
                    try:
                        supabase.table("meter_readings").insert({
                            "username": user, "date": str(reading_date), "reading": reading
                        }).execute()
                        st.success("Meter reading saved.")
                    except Exception as e:
                        st.error(f"Error: {e}")
                else:
                    st.session_state.local_data["meter_readings"].append(reading)
                    st.success("Meter reading saved.")

    # --- 7. Usage Analysis ---
    elif menu == "7. Usage analysis":
        st.header("📈 Usage Analysis")
        if not usage_logs:
            st.info("No usage data yet.")
        else:
            df = pd.DataFrame(usage_logs)
            
            st.subheader("Top Energy-Consuming Appliances")
            top_apps = df.groupby("name")["units"].sum().reset_index().sort_values(by="units", ascending=False)
            st.dataframe(top_apps, use_container_width=True)

            st.subheader("Peak Usage Hours")
            peak_hours = df.groupby("hour")["units"].sum().reset_index().sort_values(by="units", ascending=False)
            st.dataframe(peak_hours, use_container_width=True)

            st.subheader("Budget Check")
            st.write(f"Monthly Target Budget: Rs {target_budget}")
            for m in sorted(monthly_summary.keys()):
                bill_val = monthly_summary[m]["bill"]
                if bill_val > target_budget:
                    st.error(f"{m} - bill Rs {bill_val} -> EXCEEDED the budget")
                else:
                    st.success(f"{m} - bill Rs {bill_val} -> within budget")

    # --- 8. Graphs ---
    elif menu == "8. Graphs":
        st.header("📊 Graphs & Visualizations")
        if not matplotlib_ready():
            st.warning("Matplotlib is not installed.")
        elif not usage_logs:
            st.info("No usage data to draw the graph.")
        else:
            df = pd.DataFrame(usage_logs)
            
            st.subheader("Appliance-wise Energy Consumption")
            fig1, ax1 = plt.subplots(figsize=(8, 4))
            app_grouped = df.groupby("name")["units"].sum()
            app_grouped.plot(kind="bar", ax=ax1, color="steelblue")
            ax1.set_ylabel("Energy used (kWh)")
            st.pyplot(fig1)

            st.subheader("Daily kWh Usage Trend")
            fig2, ax2 = plt.subplots(figsize=(8, 4))
            daily_grouped = df.groupby("date")["units"].sum()
            daily_grouped.plot(kind="line", marker="o", color="green", ax=ax2)
            ax2.axhline(DAILY_LIMIT, color="red", linestyle="--", label=f"Daily limit ({DAILY_LIMIT} kWh)")
            ax2.set_ylabel("Energy used (kWh)")
            ax2.legend()
            st.pyplot(fig2)

    # --- 9. Set Monthly Target Budget ---
    elif menu == "9. Set monthly target budget":
        st.header("🎯 Set Monthly Target Budget")
        st.write(f"Current target budget: Rs {target_budget}")
        new_budget = st.number_input("Enter new monthly target budget (Rs)", value=float(target_budget))

        if st.button("Save Budget"):
            if new_budget <= 0:
                st.error("Budget cannot be zero or negative.")
            else:
                if supabase:
                    try:
                        existing = supabase.table("budget").select("*").eq("username", user).execute()
                        if existing.data:
                            supabase.table("budget").update({"target_budget": new_budget}).eq("username", user).execute()
                        else:
                            supabase.table("budget").insert({"username": user, "target_budget": new_budget}).execute()
                        st.success(f"Target budget saved: Rs {new_budget}")
                    except Exception as e:
                        st.error(f"Error: {e}")
                else:
                    st.session_state.local_data["budget"] = new_budget
                    st.success(f"Target budget saved: Rs {new_budget}")

    # --- 10. Data Management & Reset ---
    elif menu == "10. Data management & Reset":
        st.header("⚙️ Data Management & Reset")
        if st.button("⚠️ Clear All My Data"):
            if supabase:
                try:
                    supabase.table("appliances").delete().eq("username", user).execute()
                    supabase.table("usage_logs").delete().eq("username", user).execute()
                    supabase.table("monthly_summary").delete().eq("username", user).execute()
                    supabase.table("meter_readings").delete().eq("username", user).execute()
                    supabase.table("budget").delete().eq("username", user).execute()
                    st.success("All your account data has been cleared successfully.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error clearing data: {e}")
            else:
                st.session_state.local_data["appliances"] = [a for a in st.session_state.local_data["appliances"] if a.get("username") != user]
                st.session_state.local_data["logs"] = [l for l in st.session_state.local_data["logs"] if l.get("username") != user]
                st.success("All local data cleared successfully.")
                st.rerun()

    # --- 11. Open Project Folder ---
    elif menu == "11. Open project folder":
        st.header("📂 Project Folder Information")
        current_dir = os.getcwd()
        st.info(f"Current working directory path: `{current_dir}`")
        st.write("Note: On cloud hosting (like Streamlit Cloud), file paths refer to the remote server container.")


def matplotlib_ready():
    return plt is not None


# --- CONTROL FLOW ---
if not st.session_state.logged_in:
    login_signup_page()
else:
    main_app()
