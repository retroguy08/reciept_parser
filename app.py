from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Dict, List, Tuple
import PIL.Image
import pytesseract
import streamlit as st

st.set_page_config(
    page_title="Adaptive Receipt OCR Parser",
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
    total_items_count: int
    grand_total: float
    items: List[AggregatedItem] = field(default_factory=list)
    parsed_mode: str = "Single-Line"  # Tracks which strategy succeeded


# ==========================================
# 2. Dual-Mode Parsing Core
# ==========================================

def clean_amount(val_str: str) -> float:
    """Strips currency codes and non-numeric characters except decimals."""
    sanitized = re.sub(r"[^\d.]", "", val_str)
    try:
        return float(sanitized) if sanitized else 0.0
    except ValueError:
        return 0.0


def consolidate_items(item_dict: Dict[str, Dict[str, float]]) -> List[AggregatedItem]:
    """Converts and aggregates raw dictionary entries into typed structs."""
    items_list: List[AggregatedItem] = []
    for name, data in item_dict.items():
        items_list.append(
            AggregatedItem(
                item_name=name,
                quantity=int(data["quantity"]),
                total_price=round(data["total_price"], 2)
            )
        )
    return items_list


def parse_single_line(lines: List[str]) -> Dict[str, Dict[str, float]]:
    """Strategy A: Single-line item pattern matching (e.g., '2x Sugar 4.50' or 'Sugar 2.25')."""
    item_aggregation: Dict[str, Dict[str, float]] = {}
    line_item_pattern = r"^(?:(\d+)\s*x\s*)?(.+?)\s+[\$€Rs\.]?\s*([\d,]+\.\d{2})$"
    ignore_keywords = {"total", "subtotal", "tax", "vat", "gst", "cash", "card", "change", "balance", "amount"}

    for line in lines:
        if any(keyword in line.lower() for keyword in ignore_keywords):
            continue

        match = re.match(line_item_pattern, line, re.I)
        if match:
            qty_str, raw_name, price_str = match.groups()
            qty = int(qty_str) if qty_str else 1
            price = clean_amount(price_str)
            clean_name = raw_name.strip().title()

            if clean_name in item_aggregation:
                item_aggregation[clean_name]["quantity"] += qty
                item_aggregation[clean_name]["total_price"] += price
            else:
                item_aggregation[clean_name] = {"quantity": qty, "total_price": price}

    return item_aggregation


def parse_multi_line(lines: List[str]) -> Dict[str, Dict[str, float]]:
    """Strategy B: Multi-line pattern matching (Line N = Name, Line N+1 = Qty/Price block)."""
    item_aggregation: Dict[str, Dict[str, float]] = {}
    num_line_pattern = r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(?:Rs|Rs\.|[\$€])?\s*([\d,]+(?:\.\d+)?)$"
    stop_keywords = ["total items", "discount", "rounding", "invoice value", "sale tax", "payments", "change due"]

    for i in range(len(lines)):
        line = lines[i]
        if any(keyword in line.lower() for keyword in stop_keywords):
            break

        num_match = re.search(num_line_pattern, line, re.I)
        if num_match and i > 0:
            item_name = lines[i - 1].strip()
            if any(h in item_name.lower() for h in ["product description", "sales items", "original receipt"]):
                continue

            qty = int(float(num_match.group(1)))
            total_price = clean_amount(num_match.group(4))
            clean_name = item_name.title()

            if clean_name in item_aggregation:
                item_aggregation[clean_name]["quantity"] += qty
                item_aggregation[clean_name]["total_price"] += total_price
            else:
                item_aggregation[clean_name] = {"quantity": qty, "total_price": total_price}

    return item_aggregation


def parse_receipt_auto(raw_text: str) -> ReceiptSummary:
    """
    Adaptive engine: Attempts Single-Line parsing first; automatically falls back 
    to Multi-Line parsing if Single-Line yields no items or poor coverage.
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    # 1. Attempt Single-Line Parsing
    single_dict = parse_single_line(lines)
    
    # 2. Check threshold: If single-line parser found items, accept it; otherwise fallback
    if len(single_dict) >= 2:
        selected_dict = single_dict
        mode = "Single-Line Mode"
    else:
        # Fallback to Multi-Line Parsing
        multi_dict = parse_multi_line(lines)
        if len(multi_dict) > 0:
            selected_dict = multi_dict
            mode = "Multi-Line Mode (Fallback)"
        else:
            # If both yield < 2 items, take whichever found more
            selected_dict = single_dict if len(single_dict) >= len(multi_dict) else multi_dict
            mode = "Single-Line Mode" if len(single_dict) >= len(multi_dict) else "Multi-Line Mode"

    # Consolidate duplicate items
    items_list = consolidate_items(selected_dict)

    # 3. Extract Grand Total from footer or sum items
    grand_total = 0.0
    total_match = re.search(r"(?:invoice value|grand total|total due|total)[\s:]*(?:Rs|Rs\.|[\$€])?\s*([\d,]+\.\d{2})", raw_text, re.I)
    if total_match:
        grand_total = clean_amount(total_match.group(1))
    else:
        grand_total = round(sum(item.total_price for item in items_list), 2)

    total_items_count = sum(item.quantity for item in items_list)

    return ReceiptSummary(
        total_items_count=total_items_count,
        grand_total=grand_total,
        items=items_list,
        parsed_mode=mode
    )


# ==========================================
# 3. Streamlit Interface
# ==========================================

def main():
    st.title("🧾 Adaptive Grocery Receipt Parser")
    st.write("Upload a receipt image. The app automatically detects layout format (Single-Line vs Multi-Line) and aggregates duplicate items.")

    uploaded_file = st.file_uploader("Upload Receipt", type=["png", "jpg", "jpeg", "webp"])

    if uploaded_file is not None:
        image = PIL.Image.open(uploaded_file)
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.image(image, caption="Uploaded Receipt", use_container_width=True)

        with col2:
            with st.spinner("Executing OCR & Auto-Detecting Layout..."):
                try:
                    custom_config = r'--oem 1 --psm 6'
                    raw_text = pytesseract.image_to_string(image, config=custom_config)
                    summary = parse_receipt_auto(raw_text)
                    st.success("Receipt processed successfully!")
                except Exception as e:
                    st.error(f"OCR Execution Error: {e}")
                    return

        st.divider()

        # Summary Metrics
        st.subheader("📊 Receipt Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Detection Mode", summary.parsed_mode)
        m2.metric("Total Items Count", f"{summary.total_items_count} pcs")
        m3.metric("Grand Total Amount", f"Rs {summary.grand_total:,.2f}")

        st.divider()

        # Detailed Consolidated Table
        st.subheader("🛍️ Extracted & Consolidated Items")
        if summary.items:
            table_data = [
                {
                    "Product Description": item.item_name,
                    "Quantity": item.quantity,
                    "Unit Price": f"Rs {item.unit_price:.2f}",
                    "Total Price": f"Rs {item.total_price:.2f}"
                }
                for item in summary.items
            ]
            st.dataframe(table_data, use_container_width=True)
        else:
            st.warning("No line items extracted. Inspect raw text below.")

        with st.expander("View Raw OCR Text"):
            st.code(raw_text)

if __name__ == "__main__":
    main()
