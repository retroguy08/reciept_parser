from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Dict, List, Optional
import pandas as pd
import PIL.Image
import pytesseract
import streamlit as st

st.set_page_config(
    page_title="Universal Receipt OCR Parser & Editor",
    page_icon="🧾",
    layout="centered"
)

# ==========================================
# 1. Data Structures
# ==========================================

@dataclass
class AggregatedItem:
    item_name: str
    quantity: int
    total_price: float

    @property
    def unit_price(self) -> float:
        return round(self.total_price / self.quantity, 2) if self.quantity > 0 else 0.0


@dataclass
class ReceiptSummary:
    subtotal: float
    sales_tax: float
    grand_total: float
    total_items_count: int
    items: List[AggregatedItem] = field(default_factory=list)


# ==========================================
# 2. Resilient US/Global Receipt Parser
# ==========================================

def clean_amount(val_str: str) -> float:
    """Extracts floating point values while ignoring OCR artifacts and tax flags (FT, F, T)."""
    cleaned = re.sub(r"(?i)\b(FT|F|T|WT)\b", "", str(val_str))
    sanitized = re.sub(r"[^\d.]", "", cleaned)
    
    parts = sanitized.split(".")
    if len(parts) > 2:
        sanitized = f"{parts[0]}.{parts[1]}"
    try:
        return float(sanitized) if sanitized else 0.0
    except ValueError:
        return 0.0


def parse_lidl_us_receipt(raw_text: str) -> ReceiptSummary:
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

            price = clean_amount(price_str)
            qty = pending_qty if pending_qty > 0 else 1

            if clean_name in item_aggregation:
                item_aggregation[clean_name]["quantity"] += qty
                item_aggregation[clean_name]["total_price"] += price
            else:
                item_aggregation[clean_name] = {
                    "quantity": qty,
                    "total_price": price
                }

            pending_qty = 1

    items_list: List[AggregatedItem] = []
    for name, data in item_aggregation.items():
        items_list.append(
            AggregatedItem(
                item_name=name,
                quantity=int(data["quantity"]),
                total_price=round(data["total_price"], 2)
            )
        )

    subtotal = 0.0
    subtotal_match = re.search(r"(?:sub\s*total|subtotal)[\s:]*[\$€]?\s*([\d,]+\.\d{2})", raw_text, re.I)
    if subtotal_match:
        subtotal = clean_amount(subtotal_match.group(1))

    sales_tax = 0.0
    tax_match = re.search(r"(?:sales\s*tax|tax)[\s:]*[\$€]?\s*([\d,]+\.\d{2})", raw_text, re.I)
    if tax_match:
        sales_tax = clean_amount(tax_match.group(1))

    grand_total = 0.0
    total_match = re.search(r"(?:total\s*due|grand\s*total|total)[\s:]*[\$€]?\s*([\d,]+\.\d{2})", raw_text, re.I)
    if total_match:
        grand_total = clean_amount(total_match.group(1))
    else:
        grand_total = round(subtotal + sales_tax, 2)

    total_items_count = sum(item.quantity for item in items_list)

    return ReceiptSummary(
        subtotal=subtotal,
        sales_tax=sales_tax,
        grand_total=grand_total,
        total_items_count=total_items_count,
        items=items_list
    )


# ==========================================
# 3. Streamlit Interface
# ==========================================

def main():
    st.title("🧾 Interactive Receipt OCR Parser")
    st.write("Upload a receipt image to extract items, then manually edit or add rows if OCR misreads any text.")

    uploaded_file = st.file_uploader("Upload Receipt", type=["png", "jpg", "jpeg", "webp"])

    if uploaded_file is not None:
        image = PIL.Image.open(uploaded_file)
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.image(image, caption="Uploaded Receipt", use_container_width=True)

        with col2:
            with st.spinner("Executing OCR & Extracting Items..."):
                try:
                    custom_config = r'--oem 1 --psm 6'
                    raw_text = pytesseract.image_to_string(image, config=custom_config)
                    summary = parse_lidl_us_receipt(raw_text)
                    st.success("OCR extraction finished!")
                except Exception as e:
                    st.error(f"OCR Execution Error: {e}")
                    return

        st.divider()

        st.subheader("📝 Edit & Review Extracted Items")
        st.caption("You can directly edit item names, quantities, or prices in the table below, or click '+' to add missing items.")

        # Prepare Pandas DataFrame for st.data_editor
        initial_data = [
            {
                "Product Description": item.item_name,
                "Quantity": item.quantity,
                "Total Price ($)": item.total_price
            }
            for item in summary.items
        ]
        
        df_initial = pd.DataFrame(initial_data if initial_data else [{"Product Description": "", "Quantity": 1, "Total Price ($)": 0.0}])

        # Interactive Editable Table
        edited_df = st.data_editor(
            df_initial,
            num_rows="dynamic",  # Allows adding/deleting rows
            column_config={
                "Product Description": st.column_config.TextColumn("Product Description", required=True),
                "Quantity": st.column_config.NumberColumn("Quantity", min_value=1, step=1, required=True),
                "Total Price ($)": st.column_config.NumberColumn("Total Price ($)", min_value=0.0, format="$%.2f", required=True)
            },
            use_container_width=True
        )

        # Recalculate metrics in real-time from user's edits
        edited_df["Quantity"] = pd.to_numeric(edited_df["Quantity"], errors="coerce").fillna(0).astype(int)
        edited_df["Total Price ($)"] = pd.to_numeric(edited_df["Total Price ($)"], errors="coerce").fillna(0.0)

        updated_item_count = int(edited_df["Quantity"].sum())
        updated_subtotal = float(edited_df["Total Price ($)"].sum())
        updated_grand_total = updated_subtotal + summary.sales_tax

        st.divider()

        # Dynamic Financial Metrics Display
        st.subheader("📊 Dynamic Summary")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Items Count", f"{updated_item_count} pcs")
        m2.metric("Subtotal", f"${updated_subtotal:,.2f}")
        m3.metric("Sales Tax", f"${summary.sales_tax:,.2f}")
        m4.metric("Total Due", f"${updated_grand_total:,.2f}")

        with st.expander("View Raw OCR Text"):
            st.code(raw_text)

if __name__ == "__main__":
    main()
