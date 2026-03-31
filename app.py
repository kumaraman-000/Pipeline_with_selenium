"""
Flipkart Reviews Intelligence Dashboard
========================================
Scrapes Flipkart product reviews in real-time, enriches them with
NLP sentiment analysis, and presents them through an interactive
Streamlit dashboard with charts, word-cloud, and export options.

Run:
    streamlit run app.py
"""

from __future__ import annotations

from collections import defaultdict

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import streamlit as st
from textblob import TextBlob
from wordcloud import WordCloud

from web import FlipkartScraper

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Flipkart Reviews Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 12px; padding: 20px; color: white;
        text-align: center; margin-bottom: 10px;
    }
    .metric-card h2 { margin: 0; font-size: 2rem; }
    .metric-card p  { margin: 4px 0 0; opacity: .85; font-size: .9rem; }

    .review-card {
        background: #f8f9fa; border-left: 4px solid #667eea;
        border-radius: 8px; padding: 16px; margin-bottom: 12px;
    }
    .review-card.positive { border-left-color: #28a745; }
    .review-card.negative { border-left-color: #dc3545; }
    .review-card.neutral  { border-left-color: #ffc107; }

    .product-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        border-radius: 12px; padding: 24px; color: white; margin-bottom: 20px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── NLP helpers ───────────────────────────────────────────────────────────────

def _sentiment_label(text: str) -> str:
    if not text:
        return "Neutral"
    polarity = TextBlob(text).sentiment.polarity
    if polarity > 0.1:
        return "Positive"
    if polarity < -0.1:
        return "Negative"
    return "Neutral"


def _enrich(reviews: list[dict]) -> list[dict]:
    for r in reviews:
        r["sentiment"] = _sentiment_label(r.get("comment", ""))
    return reviews


# ── export helpers ────────────────────────────────────────────────────────────

def _to_csv(reviews: list[dict]) -> bytes:
    df = pd.DataFrame(reviews)
    cols = [c for c in ["rating", "date", "sentiment", "comment"] if c in df.columns]
    return df[cols].to_csv(index=False).encode("utf-8")



def _unique_reviews(grouped: dict[str, list[dict]]) -> list[dict]:
    seen, result = set(), []
    for items in grouped.values():
        for r in items:
            key = (r.get("comment", "") or "")[:80]
            if key and key not in seen:
                seen.add(key)
                result.append(r)
    return result


# ── PDF builder (raw PDF — no extra library needed) ──────────────────────────

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_report(product_query: str, ratings: dict, grouped_reviews: dict) -> bytes:
    lines = [
        "Flipkart Reviews Intelligence Report",
        "",
        f"Product : {product_query}",
        f"5 Star  : {ratings.get('5_star', 0)}",
        f"4 Star  : {ratings.get('4_star', 0)}",
        f"3 Star  : {ratings.get('3_star', 0)}",
        f"2 Star  : {ratings.get('2_star', 0)}",
        f"1 Star  : {ratings.get('1_star', 0)}",
        "",
    ]
    for key, title in [
        ("most_helpful", "Most Helpful"),
        ("latest", "Latest"),
        ("positive", "Positive"),
        ("negative", "Negative"),
    ]:
        lines.append(f"── {title} Reviews ──")
        lines.append("")
        for idx, r in enumerate(grouped_reviews.get(key, []), 1):
            lines.append(
                f"{idx}. [{r.get('rating') or '-'}★]  {r.get('date') or '-'}"
                f"  [{r.get('sentiment', '')}]"
            )
            comment = (r.get("comment") or "").replace("\r", " ").replace("\n", " ")
            while len(comment) > 90:
                split_at = comment.rfind(" ", 0, 90) or 90
                lines.append(f"   {comment[:split_at].strip()}")
                comment = comment[split_at:].strip()
            lines.append(f"   {comment}")
            lines.append("")

    # Paginate
    pages, cur = [], []
    for line in lines:
        cur.append(line)
        if len(cur) >= 42:
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)

    objects: list[bytes] = []

    def add(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []

    for page_lines in pages:
        content_lines = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
        for idx, line in enumerate(page_lines):
            escaped = _escape(line)
            content_lines.append(f"({'Tj' if idx == 0 else 'T* ('}...)" if False else
                                 (f"({escaped}) Tj" if idx == 0 else f"T* ({escaped}) Tj"))
        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        cid = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        pid = add((
            f"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {cid} 0 R >>"
        ).encode("ascii"))
        page_ids.append(pid)

    kids = " ".join(f"{p} 0 R" for p in page_ids)
    pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii"))
    objects[0] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")

    for pid in page_ids:
        objects[pid - 1] = objects[pid - 1].decode("ascii").replace(
            "/Parent 0 0 R", f"/Parent {pages_id} 0 R"
        ).encode("ascii")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")

    xref_off = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_off}\n%%EOF".encode("ascii")
    )
    return bytes(pdf)


# ── visualisation helpers ─────────────────────────────────────────────────────

def _wordcloud_figure(reviews: list[dict]):
    text = " ".join(r.get("comment", "") for r in reviews if r.get("comment"))
    if not text.strip():
        return None
    wc = WordCloud(
        width=900, height=400, background_color="white",
        colormap="RdYlGn", max_words=150, collocations=False,
    ).generate(text)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    fig.tight_layout(pad=0)
    return fig


def _avg_rating(reviews: list[dict]) -> float | None:
    vals = [float(r["rating"]) for r in reviews if r.get("rating") and str(r["rating"]).replace(".", "").isdigit()]
    return round(sum(vals) / len(vals), 2) if vals else None


def _sentiment_counts(reviews: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in reviews:
        counts[r.get("sentiment", "Neutral")] += 1
    return dict(counts)


# ── scraper wrapper ───────────────────────────────────────────────────────────

def run_scraper_for_query(product_query: str) -> dict:
    scraper = FlipkartScraper()
    try:
        scraper.search_product(product_query)
        data = scraper.get_product_details()
        data["product_query"] = product_query
        return data
    finally:
        scraper.driver.quit()


# ── review card renderer ──────────────────────────────────────────────────────

def render_review_cards(reviews: list[dict], section_title: str):
    st.subheader(section_title)
    if not reviews:
        st.info("No reviews available for this section.")
        return
    sentiment_icon = {"Positive": "🟢", "Negative": "🔴", "Neutral": "🟡"}
    for review in reviews:
        rating   = review.get("rating") or "-"
        date     = review.get("date") or "-"
        comment  = review.get("comment") or ""
        senti    = review.get("sentiment", "Neutral")
        icon     = sentiment_icon.get(senti, "⚪")
        css_cls  = senti.lower()
        st.markdown(
            f'<div class="review-card {css_cls}">'
            f"<b>⭐ {rating}</b> &nbsp;|&nbsp; 📅 {date} &nbsp;|&nbsp; {icon} {senti}<br>"
            f"<span style='color:#333'>{comment}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ── session state ─────────────────────────────────────────────────────────────

if "current_result" not in st.session_state:
    st.session_state["current_result"] = None

# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(
        "https://logos-world.net/wp-content/uploads/2020/11/Flipkart-Logo.png",
        use_container_width=True,
    )
    st.markdown("## 🔍 Scraper Controls")
    search_query = st.text_input("Product to search", placeholder="e.g. iPhone 13, Samsung TV")

    if st.button("🚀 Scrape Reviews", use_container_width=True, type="primary"):
        if not search_query.strip():
            st.warning("Enter a product name before scraping.")
        else:
            with st.spinner("Launching Selenium scraper… this may take ~60 seconds"):
                try:
                    st.session_state["current_result"] = run_scraper_for_query(search_query.strip())
                    st.success("Done!")
                except Exception as e:
                    st.error(f"Scraper error: {e}")
            st.rerun()

    if st.button("🗑 Clear Result", use_container_width=True):
        st.session_state["current_result"] = None
        st.rerun()

    st.markdown("---")
    st.markdown(
        "**Tech Stack**\n"
        "- 🐍 Python 3.10+\n"
        "- 🤖 Selenium 4 (headless Chrome)\n"
        "- 📊 Streamlit + Plotly\n"
        "- 🧠 TextBlob NLP\n"
        "- ☁️ Deployed on Streamlit Cloud"
    )

# ── main area ─────────────────────────────────────────────────────────────────

st.title("📊 Flipkart Reviews Intelligence Dashboard")
st.caption("Real-time product review scraping · NLP sentiment analysis · Interactive visualisations")

result = st.session_state.get("current_result")

if not result:
    st.info("👈 Search for a product from the sidebar to get started.")
    st.markdown(
        """
        ### What this dashboard does
        | Feature | Details |
        |---|---|
        | 🤖 Live Scraping | Selenium headless Chrome fetches real Flipkart reviews |
        | 🧠 Sentiment Analysis | TextBlob NLP classifies each review as Positive / Negative / Neutral |
        | ☁️ Word Cloud | Visual summary of the most frequent review words |
        | 📈 Charts | Rating distribution bar chart + sentiment pie chart |
        | 📥 Export | Download all reviews as CSV or Excel |
        | 📄 PDF Report | Generate a formatted PDF report |
        """
    )
else:
    product_query = result.get("product_query", "")
    product_name  = result.get("product_name") or product_query
    product_price = result.get("product_price", "")
    ratings       = result.get("ratings_distribution", {})
    raw_reviews   = result.get("reviews", {})

    # Enrich all reviews with sentiment
    grouped: dict[str, list[dict]] = {}
    for key in ["most_helpful", "latest", "positive", "negative"]:
        grouped[key] = _enrich(raw_reviews.get(key, []) or [])

    all_rev = _unique_reviews(grouped)
    avg_rat = _avg_rating(all_rev)
    sent_counts = _sentiment_counts(all_rev)
    pos_pct = round(sent_counts.get("Positive", 0) / max(len(all_rev), 1) * 100)

    # ── product header ────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="product-header">'
        f"<h2>🛍 {product_name}</h2>"
        f"<p style='font-size:1.4rem;color:#f0c040'>{product_price}</p>"
        f"<p style='opacity:.7'>Query: <i>{product_query}</i></p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── KPI row ───────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("⭐ Avg Rating", f"{avg_rat}/5" if avg_rat else "N/A")
    with k2:
        st.metric("💬 Total Reviews", len(all_rev))
    with k3:
        st.metric("😊 Positive", f"{pos_pct}%")
    with k4:
        st.metric("📦 In Stock", "See Flipkart")

    st.markdown("---")

    # ── charts row ────────────────────────────────────────────────────────────
    chart_col, pie_col = st.columns([3, 2])

    with chart_col:
        rating_df = pd.DataFrame([
            {"Stars": f"{s}★", "Count": ratings.get(f"{s}_star", 0)}
            for s in [5, 4, 3, 2, 1]
        ])
        fig_bar = px.bar(
            rating_df, x="Stars", y="Count",
            color="Count", color_continuous_scale="Blues",
            title="⭐ Rating Distribution",
        )
        fig_bar.update_layout(coloraxis_showscale=False, plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_bar, use_container_width=True)

    with pie_col:
        if sent_counts:
            color_map = {"Positive": "#28a745", "Negative": "#dc3545", "Neutral": "#ffc107"}
            sent_df = pd.DataFrame(
                [{"Sentiment": k, "Count": v} for k, v in sent_counts.items()]
            )
            fig_pie = px.pie(
                sent_df, names="Sentiment", values="Count",
                title="🧠 Sentiment Distribution",
                color="Sentiment", color_discrete_map=color_map,
                hole=0.4,
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    # ── word cloud ────────────────────────────────────────────────────────────
    with st.expander("☁️ Word Cloud — most frequent review words", expanded=True):
        wc_fig = _wordcloud_figure(all_rev)
        if wc_fig:
            st.pyplot(wc_fig)
        else:
            st.info("Not enough review text to generate a word cloud.")

    # ── export row ────────────────────────────────────────────────────────────
    st.markdown("### 📥 Export Reviews")
    ex1, ex2 = st.columns(2)
    with ex1:
        st.download_button(
            "⬇️ Download CSV",
            data=_to_csv(all_rev),
            file_name=f"{product_query.replace(' ', '_')}_reviews.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with ex2:
        pdf_bytes = build_pdf_report(product_query, ratings, grouped)
        st.download_button(
            "⬇️ Download PDF Report",
            data=pdf_bytes,
            file_name=f"{product_query.replace(' ', '_')}_report.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    st.markdown("---")

    # ── review tabs ───────────────────────────────────────────────────────────
    tabs = st.tabs(["🏆 Most Helpful", "🕐 Latest", "👍 Positive", "👎 Negative"])
    tab_map = [
        ("most_helpful", "Most Helpful Reviews"),
        ("latest",       "Latest Reviews"),
        ("positive",     "Positive Reviews"),
        ("negative",     "Negative Reviews"),
    ]
    for tab, (key, label) in zip(tabs, tab_map):
        with tab:
            items = grouped.get(key, [])
            st.caption(f"{len(items)} reviews in this category")
            render_review_cards(items, label)
