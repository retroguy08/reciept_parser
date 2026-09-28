from dataclasses import dataclass, field
from datetime import datetime
import io
import re
from typing import Dict, List
import PIL.Image
import pytesseract
import streamlit as st

# Set up page configuration
st.set_page_config(
    page_title="Grocery Receipt OCR & Summarizer",
    page_icon="🛒",
    layout="centered"
)


# ==========================================
# 1. Data Schemas
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
    merchant_name: str
    date: str
    subtotal: float
    tax: float
    grand_total: float
    total_items_count: int
    items: List[AggregatedItem] = field(default_factory=list)


# ==========================================
# 2. Text Parsing & Deduction Logic
# ==========================================

def clean_amount(val_str: str) -> float:
    """Strips non-numeric characters except decimals and converts to float."""
    sanitized = re.sub(r"[^\d.]", "", val_str)
    try:
        return float(sanitized) if sanitized else 0.0
    except ValueError:
        return 0.0


def parse_and_aggregate_receipt(raw_text: str) -> ReceiptSummary:
    """
    Parses OCR raw text, aggregates duplicate line items (sums quantities and total costs),
    and computes overall expenditure metrics.
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    # Extract Merchant Name
    merchant_name = "Unknown Store"
    for line in lines[:5]:
        if re.search(r"[a-zA-Z]", line) and not re.search(r"(receipt|welcome|thank|store)", line, re.I):
            merchant_name = line.strip()
            break

    # Extract Date
    date_pattern = r"\b(\d{1,2}[/\.-]\d{1,2}[/\.-]\d{2,4}|\d{4}[/\.-]\d{1,2}[/\.-]\d{1,2})\b"
    date_match = re.search(date_pattern, raw_text)
    receipt_date = date_match.group(1) if date_match else datetime.today().strftime("%Y-%m-%d")

    # Extract Explicit Totals
    tax = 0.0
    tax_match = re.search(r"(?:tax|vat|gst)[\s:]*[\$€Rs\.]*\s*([\d,]+\.\d{2})", raw_text, re.I)
    if tax_match:
        tax = clean_amount(tax_match.group(1))

    grand_total_explicit = 0.0
    total_match = re.search(r"(?:grand\s*total|total\s*due|total)[\s:]*[\$€Rs\.]*\s*([\d,]+\.\d{2})", raw_text, re.I)
    if total_match:
        grand_total_explicit = clean_amount(total_match.group(1))

    # Pattern for line items: optional quantity prefix, item name, and price
    # e.g., "2 x Sugar 4.50" or "Sugar 2.25"
    line_item_pattern = r"^(?:(\d+)\s*x\s*)?(.+?)\s+[\$€Rs\.]?\s*([\d,]+\.\d{2})$"
    ignore_keywords = {"total", "subtotal", "tax", "vat", "gst", "cash", "card", "change", "balance", "amount"}

    # Dictionary used to consolidate duplicate items by normalized item name
    item_aggregation: Dict[str, Dict[str, float]] = {}

    for line in lines:
        if any(keyword in line.lower() for keyword in ignore_keywords):
            continue

        match = re.match(line_item_pattern, line, re.I)
        if match:
            qty_str, raw_name, price_str = match.groups()
            parsed_qty = int(qty_str) if qty_str else 1
            parsed_price = clean_amount(price_str)

            # Normalize item key to consolidate duplicates like "Sugar" and "SUGAR"
            clean_name = raw_name.strip().title()

            if clean_name in item_aggregation:
                item_aggregation[clean_name]["quantity"] += parsed_qty
                item_aggregation[clean_name]["total_price"] += parsed_price
            else:
                item_aggregation[clean_name] = {
                    "quantity": parsed_qty,
                    "total_price": parsed_price
                }

    # Convert aggregated dictionary back to strong data structures
    aggregated_items_list: List[AggregatedItem] = []
    for name, details in item_aggregation.items():
        aggregated_items_list.append(
            AggregatedItem(
                item_name=name,
                quantity=details["quantity"],
                total_price=round(details["total_price"], 2)
            )
        )

    # Calculate overall item counts and prices
    computed_subtotal = round(sum(item.total_price for item in aggregated_items_list), 2)
    total_items_count = sum(item.quantity for item in aggregated_items_list)

    final_grand_total = grand_total_explicit if grand_total_explicit > 0.0 else round(computed_subtotal + tax, 2)

    return ReceiptSummary(
        merchant_name=merchant_name,
        date=receipt_date,
        subtotal=computed_subtotal,
        tax=tax,
        grand_total=final_grand_total,
        total_items_count=total_items_count,
        items=aggregated_items_list
    )


# ==========================================
# 3. Streamlit User Interface
# ==========================================

def main():
    st.title("🛒 Grocery Receipt OCR & Summarizer")
    st.write("Upload a receipt or grocery bill image to automatically extract items, adjust duplicate quantities, and total costs.")

    uploaded_file = st.file_uploader(
        "Choose a receipt image...",
        type=["png", "jpg", "jpeg", "webp"]
    )

    if uploaded_file is not None:
        # Display uploaded image preview
        image = PIL.Image.open(uploaded_file)
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.image(image, caption="Uploaded Receipt", use_container_width=True)

        with col2:
            with st.spinner("Extracting text and calculating totals..."):
                try:
                    # Execute OCR in memory
                    raw_ocr_text = pytesseract.image_to_string(image)
                    summary = parse_and_aggregate_receipt(raw_ocr_text)

                    st.success("Receipt processed successfully!")

                except Exception as e:
                    st.error(f"Error executing OCR: {e}")
                    st.info("Ensure Tesseract binary is installed and configured on your environment PATH.")
                    return

        st.divider()

        # Key High-Level Metrics
        st.subheader("📊 Expense Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Store", summary.merchant_name)
        m2.metric("Total Items Count", f"{summary.total_items_count} pcs")
        m3.metric("Grand Total Cost", f"${summary.grand_total:.2f}")

        st.divider()

        # Detailed Consolidated Line Items Table
        st.subheader("🛍️ Consolidated Grocery List")
        if summary.items:
            # Build displayable dictionary list for Streamlit dataframe visualization
            table_data = [
                {
                    "Item Name": item.item_name,
                    "Total Quantity": item.quantity,
                    "Est. Unit Price": f"${item.unit_price:.2f}",
                    "Total Cost": f"${item.total_price:.2f}"
                }
                for item in summary.items
            ]
            st.dataframe(table_data, use_container_width=True)
        else:
            st.warning("No line items detected clearly. Raw text output shown below.")

        # Expandable tab to view raw OCR output for debugging
        with st.expander("View Raw OCR Extracted Text"):
            st.text(raw_ocr_text)


if __name__ == "__main__":
    main()