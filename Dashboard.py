"""
Dashboard.py - Temporary Streamlit dashboard for Flipkart product review scraping.

Run:
    streamlit run Pipeline_with_selenium/Dashboard.py
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import plotly.express as px
import streamlit as st

from web import FlipkartScraper


def run_scraper_for_query(product_query: str) -> dict:
    scraper = FlipkartScraper()
    try:
        scraper.search_product(product_query)
        scraped_data = scraper.get_product_details()
        scraped_data["product_query"] = product_query
        scraped_data["product_url"] = scraper.driver.current_url
        return scraped_data
    finally:
        scraper.driver.quit()


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_report(product_query: str, ratings: dict, grouped_reviews: dict[str, list[dict]]) -> bytes:
    lines = [
        "Flipkart Reviews Report",
        "",
        f"Product: {product_query}",
        f"5 Star: {ratings.get('5_star', 0)}",
        f"4 Star: {ratings.get('4_star', 0)}",
        f"3 Star: {ratings.get('3_star', 0)}",
        f"2 Star: {ratings.get('2_star', 0)}",
        f"1 Star: {ratings.get('1_star', 0)}",
        "",
    ]

    section_map = [
        ("most_helpful", "Most Helpful"),
        ("latest", "Latest"),
        ("positive", "Positive"),
        ("negative", "Negative"),
    ]
    for key, title in section_map:
        lines.append(f"{title} Reviews")
        lines.append("")
        reviews = grouped_reviews.get(key, [])
        if not reviews:
            lines.append("No reviews available.")
            lines.append("")
            continue
        for idx, review in enumerate(reviews, start=1):
            lines.append(f"{idx}. Rating: {review.get('rating') or '-'}")
            lines.append(f"   Date: {review.get('date') or '-'}")
            comment = (review.get("comment") or "").replace("\r", " ").replace("\n", " ")
            while len(comment) > 95:
                split_at = comment.rfind(" ", 0, 95)
                if split_at <= 0:
                    split_at = 95
                lines.append(f"   Comment: {comment[:split_at].strip()}")
                comment = comment[split_at:].strip()
            lines.append(f"   Comment: {comment}")
            lines.append("")

    pages = []
    current_page = []
    max_lines_per_page = 42
    for line in lines:
        current_page.append(line)
        if len(current_page) >= max_lines_per_page:
            pages.append(current_page)
            current_page = []
    if current_page:
        pages.append(current_page)

    objects = []
    page_ids = []

    def add_object(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    add_object(b"")
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_entries = []
    for page_lines in pages:
        content_lines = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
        for idx, line in enumerate(page_lines):
            if idx == 0:
                content_lines.append(f"({_escape_pdf_text(line)}) Tj")
            else:
                content_lines.append(f"T* ({_escape_pdf_text(line)}) Tj")
        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        content_id = add_object(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_id = add_object(
            (
                f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)
        page_entries.append(f"{page_id} 0 R")

    pages_id = add_object(
        f"<< /Type /Pages /Kids [{' '.join(page_entries)}] /Count {len(page_entries)} >>".encode("ascii")
    )
    objects[0] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")

    for page_id in page_ids:
        page_obj = objects[page_id - 1].decode("ascii")
        objects[page_id - 1] = page_obj.replace("/Parent 0 0 R", f"/Parent {pages_id} 0 R").encode("ascii")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )
    return bytes(pdf)


def render_review_cards(reviews: list[dict], section_title: str):
    st.subheader(section_title)
    if not reviews:
        st.info("No reviews available for this section.")
        return

    for review in reviews:
        rating = review.get("rating") or "-"
        review_date = review.get("date") or "-"
        comment = review.get("comment") or ""
        st.markdown(f"**Rating:** {rating}  |  **Date:** {review_date}")
        st.write(comment)
        st.markdown("---")


st.set_page_config(
    page_title="Flipkart Reviews Dashboard",
    page_icon="F",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "current_result" not in st.session_state:
    st.session_state["current_result"] = None

st.title("Flipkart Product Reviews Dashboard")
st.caption("Temporary mode: nothing is saved to the database. Refresh or clear to remove the current result.")

with st.sidebar:
    st.header("Scraper Controls")
    search_query = st.text_input("Product to search", placeholder="iphone 12")

    if st.button("Scrape Product", use_container_width=True):
        if not search_query.strip():
            st.warning("Enter a product name before scraping.")
        else:
            with st.spinner("Running Selenium scraper..."):
                st.session_state["current_result"] = run_scraper_for_query(search_query.strip())
            st.rerun()

    if st.button("Clear Current Result", use_container_width=True):
        st.session_state["current_result"] = None
        st.rerun()

result = st.session_state.get("current_result")

if not result:
    st.info("Search for a product from the sidebar. Data is temporary and is not stored in MySQL.")
else:
    product_query = result.get("product_query", "")
    ratings = result.get("ratings_distribution", {})
    reviews = result.get("reviews", {})

    grouped_reviews: dict[str, list[dict]] = defaultdict(list)
    for key in ["most_helpful", "latest", "positive", "negative"]:
        grouped_reviews[key] = reviews.get(key, []) or []

    st.header(f"Snapshot Details: {product_query}")

    rating_df = pd.DataFrame(
        [
            {"Stars": "5 Star", "Count": ratings.get("5_star", 0)},
            {"Stars": "4 Star", "Count": ratings.get("4_star", 0)},
            {"Stars": "3 Star", "Count": ratings.get("3_star", 0)},
            {"Stars": "2 Star", "Count": ratings.get("2_star", 0)},
            {"Stars": "1 Star", "Count": ratings.get("1_star", 0)},
        ]
    )

    chart_col, summary_col = st.columns([2, 1])
    with chart_col:
        fig = px.bar(
            rating_df,
            x="Stars",
            y="Count",
            color="Count",
            color_continuous_scale="Blues",
            title="Rating Distribution",
        )
        fig.update_layout(coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with summary_col:
        total_reviews = sum(len(items) for items in grouped_reviews.values())
        st.metric("Saved In DB", "No")
        st.metric("Review Rows Shown", f"{total_reviews:,}")
        pdf_bytes = build_pdf_report(product_query, ratings, grouped_reviews)
        st.download_button(
            "Download PDF Report",
            data=pdf_bytes,
            file_name=f"{product_query.replace(' ', '_').lower()}_report.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    tabs = st.tabs(["Most Helpful", "Latest", "Positive", "Negative"])
    tab_keys = [
        ("most_helpful", "Most Helpful Reviews"),
        ("latest", "Latest Reviews"),
        ("positive", "Positive Reviews"),
        ("negative", "Negative Reviews"),
    ]
    for tab, (review_type, label) in zip(tabs, tab_keys):
        with tab:
            st.caption(f"{len(grouped_reviews.get(review_type, []))} reviews in this category")
            render_review_cards(grouped_reviews.get(review_type, []), label)
