import io
import re
from pathlib import Path

import pandas as pd
import streamlit as st

APP_TITLE = "Bundle Pricing (Private)"
DEFAULT_DATA_TIERS_GB = [1, 2, 3, 5, 10, 15, 20, 30, 50]

REQUIRED_COLUMNS = ["Country", "Per GB", "SMS", "LOCAL", "ROW", "VOICE MT", "ISO"]


def _normalize_operator_sheet(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    keep = [c for c in REQUIRED_COLUMNS if c in df.columns]
    df = df[keep]

    df = df[df["Country"].notna()].copy()
    df["Country"] = df["Country"].astype(str).str.strip()

    if "ISO" in df.columns:
        df["ISO"] = df["ISO"].astype(str).str.strip().str.upper()

    for c in ["Per GB", "SMS", "LOCAL", "ROW", "VOICE MT"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["Country", "Per GB"])
    return df


@st.cache_data(show_spinner=False)
def load_workbook(file_bytes: bytes) -> dict[str, pd.DataFrame]:
    """
    Return {operator_name: normalized_df}.

    We treat any sheet named only with digits (e.g., 106, 124, 142, 161) as an "operator sheet"
    and keep it if it contains the required columns.
    """
    xl = pd.ExcelFile(io.BytesIO(file_bytes))
    out: dict[str, pd.DataFrame] = {}

    for sheet in xl.sheet_names:
        if not re.fullmatch(r"\d+", str(sheet).strip()):
            continue
        df = pd.read_excel(xl, sheet)
        df.columns = [str(c).strip() for c in df.columns]
        if "Country" not in df.columns or "Per GB" not in df.columns:
            continue
        out[str(sheet).strip()] = _normalize_operator_sheet(df)

    if not out:
        raise ValueError("No operator sheets found. Expected sheets like 106/124/142/161 containing Country + Per GB.")
    return out


def round_price(x: float, rounding: str, step: float) -> float:
    if rounding == "None" or step <= 0:
        return float(x)

    import math

    q = x / step
    if rounding == "Up (ceil)":
        return float(math.ceil(q) * step)
    if rounding == "Down (floor)":
        return float(math.floor(q) * step)
    # Nearest
    return float(round(q) * step)


def compute_prices(
    per_gb: float,
    sms_unit: float,
    voice_unit: float,
    margin_pct: float,
    margin_mode: str,
    data_tiers: list[int],
    minutes: int,
    sms_count: int,
    rounding: str,
    step: float,
) -> pd.DataFrame:
    rows = []
    for gb in data_tiers:
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
                "Data (GB)": int(gb),
                "Minutes": int(minutes),
                "SMS": int(sms_count),
                "Base cost": round(float(base_cost), 4),
                "Sell price": round(float(sell_rounded), 4),
                "Profit": round(float(sell_rounded - base_cost), 4),
            }
        )
    return pd.DataFrame(rows)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Bundles")
    return bio.getvalue()


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)

    st.caption(
        "Local tool: upload your Excel pricing database, pick Operator + Country, add margin, generate bundles."
    )

    uploaded = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    if not uploaded:
        st.info("Upload your Excel file to start.")
        st.stop()

    operators = load_workbook(uploaded.getvalue())
    op = st.selectbox("Operator (sheet)", sorted(operators.keys(), key=lambda x: int(x)))
    df = operators[op]

    country = st.selectbox("Country", sorted(df["Country"].unique().tolist()))
    row = df.loc[df["Country"] == country].iloc[0]

    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("Unit costs (from Excel)")
        st.write(
            {
                "Per GB": float(row["Per GB"]),
                "SMS": float(row["SMS"]),
                "Voice (LOCAL)": float(row["LOCAL"]),
                "Voice (ROW)": float(row["ROW"]),
                "Voice (MT)": float(row["VOICE MT"]),
                "ISO": row.get("ISO", ""),
            }
        )

    with col2:
        st.subheader("Pricing inputs")
        margin_pct = st.number_input("Profit / Markup (%)", min_value=0.0, max_value=95.0, value=20.0, step=1.0)
        margin_mode = st.radio("Interpretation", ["Markup on cost", "Profit margin of sell price"], horizontal=True)

        voice_type = st.selectbox("Voice unit to use", ["LOCAL", "ROW", "VOICE MT"])
        voice_unit = float(row[voice_type])

        minutes = st.number_input("Included minutes (optional)", min_value=0, value=0, step=1)
        sms_count = st.number_input("Included SMS (optional)", min_value=0, value=0, step=1)

        tiers = st.multiselect(
            "Data tiers (GB)",
            options=DEFAULT_DATA_TIERS_GB,
            default=[1, 3, 5, 10],
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
        minutes=int(minutes),
        sms_count=int(sms_count),
        rounding=rounding,
        step=float(step),
    )

    st.subheader("Generated bundles")
    st.dataframe(result, use_container_width=True)

    # Downloads
    csv_bytes = result.to_csv(index=False).encode("utf-8")
    xlsx_bytes = to_excel_bytes(result)

    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button("Download CSV", data=csv_bytes, file_name=f"bundles_{op}_{country}.csv", mime="text/csv")
    with c2:
        st.download_button(
            "Download Excel",
            data=xlsx_bytes,
            file_name=f"bundles_{op}_{country}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
