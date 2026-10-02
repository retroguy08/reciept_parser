from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Dict, List, Optional
import pandas as pd
import PIL.Image
import pytesseract
import streamlit as st

# ==========================================
# 1. Page Config & Custom Styling
# ==========================================

st.set_page_config(
    page_title="Smart Receipt Analytics",
    page_icon="🧾",
    layout="wide"
)

# Inject CSS for polished dashboard visuals
st.markdown("""
<style>
    /* Metric Card Styling */
    div[data-testid="stMetric"] {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        padding: 15px 20px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    }
    div[data-testid="stMetric"] label {
        font-weight: 600;
        color: #495057;
    }
    /* Main Header Styling */
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# 2. Data Structures
# ==========================================

@dataclass
class AggregatedItem:
    item_name: str
    quantity: int
    unit_price: float

    @property
    def total_price(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class ReceiptSummary:
    subtotal: float
    sales_tax: float
    grand_total: float
    total_items_count: int
    items: List[AggregatedItem] = field(default_factory=list)


# ==========================================
# 3. OCR Parser Logic
# ==========================================

def clean_amount(val_str: str) -> float:
    """Strips non-numeric noise and converts text to float."""
    cleaned = re.sub(r"(?i)\b(FT|F|T|WT)\b", "", str(val_str))
    sanitized = re.sub(r"[^\d.]", "", cleaned)
    
    parts = sanitized.split(".")
    if len(parts) > 2:
        sanitized = f"{parts[0]}.{parts[1]}"
    try:
        return float(sanitized) if sanitized else 0.0
    except ValueError:
        return 0.0


def parse_receipt(raw_text: str) -> ReceiptSummary:
    raw_lines = raw_text.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]

    item_aggregation: Dict[str, Dict[str, float]] = {}
    stop_keywords = [
        "sub total", "subtotal", "sales tax", "total due", 
        "card", "change", "total savings", "thank you", "mylidl"
    ]

    pending_qty: int = 1

    for line in lines:
        line_lower = line.lower()
        if any(keyword in line_lower for keyword in stop_keywords):
            break

        # Check for unit multiplier lines (e.g., "2.0 @ 2.99" or "4.02 lb @ $0.59/lb")
        qty_modifier_match = re.search(r"^(\d+(?:\.\d+)?)\s*(?:lb|pcs|x)?\s*@\s*[\$€]?\s*([\d.]+)", line, re.I)
        if qty_modifier_match:
            try:
                pending_qty = int(float(qty_modifier_match.group(1)))
            except ValueError:
                pending_qty = 1
            continue

        item_match = re.search(r"^(.+?)\s+([\d]+\.[\d]{2})\s*(?:FT|F|T|WT|py|fF|pr)?$", line, re.I)

        if item_match:
            raw_name, price_str = item_match.groups()
            clean_name = re.sub(r"[{}|~‘'\"\[\]]", "", raw_name).strip().title()

            if any(h in clean_name.lower() for h in ["welcome", "store", "organic", "item"]):
                if not re.search(r"\d", clean_name):
                    continue

            total_line_price = clean_amount(price_str)
            qty = pending_qty if pending_qty > 0 else 1
            unit_price = round(total_line_price / qty, 2) if qty > 0 else total_line_price

            if clean_name in item_aggregation:
                item_aggregation[clean_name]["quantity"] += qty
            else:
                item_aggregation[clean_name] = {
                    "quantity": qty,
                    "unit_price": unit_price
                }

            pending_qty = 1

    items_list: List[AggregatedItem] = []
    for name, data in item_aggregation.items():
        items_list.append(
            AggregatedItem(
                item_name=name,
                quantity=int(data["quantity"]),
                unit_price=float(data["unit_price"])
            )
        )

    sales_tax = 0.0
    tax_match = re.search(r"(?:sales\s*tax|tax)[\s:]*[\$€]?\s*([\d,]+\.\d{2})", raw_text, re.I)
    if tax_match:
        sales_tax = clean_amount(tax_match.group(1))

    subtotal = sum(item.total_price for item in items_list)
    grand_total = subtotal + sales_tax
    total_items_count = sum(item.quantity for item in items_list)

    return ReceiptSummary(
        subtotal=subtotal,
        sales_tax=sales_tax,
        grand_total=grand_total,
        total_items_count=total_items_count,
        items=items_list
    )


# ==========================================
# 4. Streamlit Application
# ==========================================

def main():
    st.markdown('<div class="main-title">🧾 Smart Receipt Analytics & Parser</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Automated OCR Extraction with Multiplied Unit-Pricing and Real-Time Interactive Editing.</div>', unsafe_allow_html=True)

    # Sidebar for control options
    st.sidebar.header("⚙️ OCR Settings")
    psm_mode = st.sidebar.selectbox("Tesseract Page Segmentation Mode", ["PSM 6 (Single Uniform Block)", "PSM 4 (Column Detection)"], index=0)
    config_flag = r'--oem 1 --psm 6' if "PSM 6" in psm_mode else r'--oem 1 --psm 4'

    uploaded_file = st.file_uploader("Upload Receipt / Bill Image", type=["png", "jpg", "jpeg", "webp"])

    if uploaded_file is not None:
        image = PIL.Image.open(uploaded_file)
        
        # Tabs layout for structured user journey
        tab1, tab2 = st.tabs(["📑 Review & Edit Items", "🔍 Raw OCR Output"])

        with tab1:
            col_img, col_data = st.columns([1, 1.3], gap="medium")

            with col_img:
                st.subheader("🖼️ Document Preview")
                st.image(image, use_container_width=True)

            with col_data:
                st.subheader("✏️ Interactive Expense Table")
                st.caption("Editing **Quantity** or **Unit Price** automatically re-calculates the **Total Price** and metrics.")

                with st.spinner("Extracting text and calculating line items..."):
                    try:
                        raw_text = pytesseract.image_to_string(image, config=config_flag)
                        summary = parse_receipt(raw_text)
                    except Exception as e:
                        st.error(f"OCR Processing Error: {e}")
                        return

                # Build initial structure for data_editor
                initial_data = [
                    {
                        "Product Description": item.item_name,
                        "Quantity": item.quantity,
                        "Unit Price ($)": item.unit_price,
                    }
                    for item in summary.items
                ]

                df_initial = pd.DataFrame(
                    initial_data if initial_data else [{"Product Description": "", "Quantity": 1, "Unit Price ($)": 0.0}]
                )

                # Editable interactive table
                edited_df = st.data_editor(
                    df_initial,
                    num_rows="dynamic",
                    column_config={
                        "Product Description": st.column_config.TextColumn("Product Description", required=True),
                        "Quantity": st.column_config.NumberColumn("Quantity", min_value=1, step=1, required=True),
                        "Unit Price ($)": st.column_config.NumberColumn("Unit Price ($)", min_value=0.0, format="$%.2f", required=True)
                    },
                    use_container_width=True
                )

                # Automatic Multiplication: Quantity x Unit Price = Line Total
                edited_df["Quantity"] = pd.to_numeric(edited_df["Quantity"], errors="coerce").fillna(0).astype(int)
                edited_df["Unit Price ($)"] = pd.to_numeric(edited_df["Unit Price ($)"], errors="coerce").fillna(0.0)
                edited_df["Line Total ($)"] = edited_df["Quantity"] * edited_df["Unit Price ($)"]

                # Display calculated line totals view below
                st.markdown("##### 🛒 Calculated Itemized Breakdown")
                st.dataframe(
                    edited_df[["Product Description", "Quantity", "Unit Price ($)", "Line Total ($)"]],
                    column_config={
                        "Unit Price ($)": st.column_config.NumberColumn(format="$%.2f"),
                        "Line Total ($)": st.column_config.NumberColumn(format="$%.2f")
                    },
                    use_container_width=True
                )

        # Dynamic KPI metric cards calculation
        updated_item_count = int(edited_df["Quantity"].sum())
        updated_subtotal = float(edited_df["Line Total ($)"].sum())
        updated_grand_total = updated_subtotal + summary.sales_tax

        st.divider()

        st.subheader("📊 Dynamic Financial Dashboard")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Items Count", f"{updated_item_count} pcs")
        m2.metric("Calculated Subtotal", f"${updated_subtotal:,.2f}")
        m3.metric("Sales Tax", f"${summary.sales_tax:,.2f}")
        m4.metric("Grand Total Due", f"${updated_grand_total:,.2f}")

        # CSV Export feature
        st.markdown("<br>", unsafe_allow_html=True)
        csv_data = edited_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Export Expense Report (CSV)",
            data=csv_data,
            file_name=f"receipt_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

        with tab2:
            st.subheader("🔍 OCR Text Output")
            st.code(raw_text)

if __name__ == "__main__":
    main()
