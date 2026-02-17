import io
import pandas as pd
import streamlit as st

APP_TITLE = "Bundle Pricing"
DEFAULT_DATA_TIERS_GB = [1, 2, 3, 5, 10, 15, 20, 30, 50]
DEFAULT_VALIDITY_DAYS = [1, 3, 5, 7, 10, 15, 30, 60, 90]

REQUIRED_COLUMNS = [
    "Country",
    "Per GB",
    "SMS",
    "Voice (LOCAL)",
    "Voice (ROW)",
    "Voice (MT)",
]


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in Excel: {missing}")

    df = df[df["Country"].notna()].copy()
    df["Country"] = df["Country"].astype(str).str.strip()

    for c in ["Per GB", "SMS", "Voice (LOCAL)", "Voice (ROW)", "Voice (MT)"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["Country", "Per GB"])
    return df


@st.cache_data(show_spinner=False)
def load_single_sheet(file_bytes: bytes) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0)
    return _normalize_df(df)


def round_price(x: float, rounding: str, step: float) -> float:
    if rounding == "None" or step <= 0:
        return float(x)

    import math
    q = x / step
    if rounding == "Up (ceil)":
        return float(math.ceil(q) * step)
    if rounding == "Down (floor)":
        return float(math.floor(q) * step)
    return float(round(q) * step)  # Nearest


def compute_prices(
    per_gb: float,
    sms_unit: float,
    voice_unit: float,
    margin_pct: float,
    margin_mode: str,
    data_tiers: list[int],
    validity_days_list: list[int],
    minutes: int,
    sms_count: int,
    rounding: str,
    step: float,
) -> pd.DataFrame:
    rows = []
    for validity_days in validity_days_list:
        for gb in data_tiers:
            # NOTE: Validity doesn't change cost here (just labels the package).
            base_cost = (gb * per_gb) + (minutes * voice_unit) + (sms_count * sms_unit)

            if margin_mode == "Markup on cost":
                sell = base_cost * (1 + margin_pct / 100.0)
            else:
                # Profit margin as % of SELLING price: price = cost / (1 - margin)
                m = margin_pct / 100.0
                sell = base_cost / (1 - m) if m < 1 else float("nan")

            sell_rounded = round_price(sell, rounding, step)
            rows.append(
                {
                    "Validity (Days)": int(validity_days),
                    "Data (GB)": int(gb),
                    "Minutes": int(minutes),
                    "SMS": int(sms_count),
                    "Base cost": round(float(base_cost), 4),
                    "Sell price": round(float(sell_rounded), 4),
                    "Profit": round(float(sell_rounded - base_cost), 4),
                }
            )

    out = pd.DataFrame(rows)
    out = out.sort_values(["Validity (Days)", "Data (GB)"]).reset_index(drop=True)
    return out


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Bundles")
    return bio.getvalue()


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Upload Excel → choose Country → choose Validity + GB → enter margin → generate bundles → download.")

    uploaded = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    if not uploaded:
        st.info("Upload your Excel file to start.")
        st.stop()

    df = load_single_sheet(uploaded.getvalue())

    country = st.selectbox("Country", sorted(df["Country"].unique().tolist()))
    row = df.loc[df["Country"] == country].iloc[0]

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Unit costs (from Excel)")
        st.write(
            {
                "Per GB": float(row["Per GB"]),
                "SMS": float(row["SMS"]),
                "Voice (LOCAL)": float(row["Voice (LOCAL)"]),
                "Voice (ROW)": float(row["Voice (ROW)"]),
                "Voice (MT)": float(row["Voice (MT)"]),
            }
        )

    with col2:
        st.subheader("Pricing inputs")
        margin_pct = st.number_input(
            "Profit / Markup (%)", min_value=0.0, max_value=95.0, value=20.0, step=1.0
        )
        margin_mode = st.radio(
            "Interpretation", ["Markup on cost", "Profit margin of sell price"], horizontal=True
        )

        voice_type = st.selectbox("Voice unit to use", ["Voice (LOCAL)", "Voice (ROW)", "Voice (MT)"])
        voice_unit = float(row[voice_type])

        minutes = st.number_input("Included minutes (optional)", min_value=0, value=0, step=1)
        sms_count = st.number_input("Included SMS (optional)", min_value=0, value=0, step=1)

        # ✅ NEW: Validity selection
        validity_days = st.multiselect(
            "Validity / Time range (days)",
            options=DEFAULT_VALIDITY_DAYS,
            default=[7, 30],
        )
        validity_days = sorted(set(int(x) for x in validity_days)) if validity_days else [7, 30]

        tiers = st.multiselect(
            "Data tiers (GB)", options=DEFAULT_DATA_TIERS_GB, default=[1, 3, 5, 10]
        )
        tiers = sorted(set(int(x) for x in tiers)) if tiers else [1, 3, 5, 10]

        rounding = st.selectbox("Rounding", ["None", "Nearest", "Up (ceil)", "Down (floor)"], index=2)
        step = st.selectbox("Round step", [0.01, 0.05, 0.1, 0.5, 1.0], index=0)

    result = compute_prices(
        per_gb=float(row["Per GB"]),
        sms_unit=float(row["SMS"]),
        voice_unit=voice_unit,
        margin_pct=float(margin_pct),
        margin_mode=margin_mode,
        data_tiers=tiers,
        validity_days_list=validity_days,
        minutes=int(minutes),
        sms_count=int(sms_count),
        rounding=rounding,
        step=float(step),
    )

    st.subheader("Generated bundles")
    st.dataframe(result, use_container_width=True)

    csv_bytes = result.to_csv(index=False).encode("utf-8")
    xlsx_bytes = to_excel_bytes(result)

    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button("Download CSV", data=csv_bytes, file_name=f"bundles_{country}.csv", mime="text/csv")
    with c2:
        st.download_button(
            "Download Excel",
            data=xlsx_bytes,
            file_name=f"bundles_{country}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
