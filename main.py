import os
import re
from datetime import datetime, date, timedelta, timezone
from typing import List, Dict, Tuple

import pandas as pd
import praw
import streamlit as st
from dotenv import load_dotenv
import plotly.express as px
import plotly.graph_objects as go
from security import check_password


# ────────────────────────────── env & reddit init ──────────────────────────────
if os.path.exists(".env"):  # load only in local/dev
    load_dotenv()

def init_reddit() -> praw.Reddit:
    """Create a PRAW `Reddit` instance from env / Streamlit secrets.
    Shows a helpful message and stops if credentials are missing.
    """
    client_id     = os.getenv("REDDIT_CLIENT_ID")     or st.secrets.get("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET") or st.secrets.get("REDDIT_CLIENT_SECRET")
    user_agent    = os.getenv("REDDIT_USER_AGENT")    or st.secrets.get("REDDIT_USER_AGENT")

    missing: list[str] = []
    if not client_id:
        missing.append("REDDIT_CLIENT_ID")
    if not client_secret:
        missing.append("REDDIT_CLIENT_SECRET")
    if not user_agent:
        missing.append("REDDIT_USER_AGENT")

    if missing:
        st.error(
            "Missing Reddit API credentials: " + ", ".join(missing) +
            "\n\nPlease add them in Settings → Secrets (or set environment variables)."
        )
        st.markdown(
            "Add these entries in your Streamlit secrets:")
        st.code(
            """REDDIT_CLIENT_ID = "your_actual_client_id"
REDDIT_CLIENT_SECRET = "your_actual_client_secret"
REDDIT_USER_AGENT = "RedditScraper/1.0 by /u/yourusername""",
            language="toml",
        )
        st.stop()

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


# ──────────────────────────── Intelligent Categorization ────────────────────────────
def get_category_keywords() -> Dict[str, List[str]]:
    """Define keywords and patterns for each category like GummySearch."""
    return {
        "Pain Points": [
            "problem", "issue", "struggling", "frustrated", "annoying", "broken", "doesn't work",
            "hate", "terrible", "awful", "worst", "failing", "difficult", "hard", "impossible",
            "bug", "error", "crash", "slow", "expensive", "overpriced", "waste", "scam",
            "disappointed", "regret", "mistake", "wrong", "bad", "horrible", "sucks",
            "fix", "solve", "help", "support", "trouble", "stuck", "confused", "lost"
        ],
        "Solution Requests": [
            "how to", "how do", "how can", "what's the best", "recommend", "suggestion",
            "advice", "help me", "looking for", "need", "want", "seeking", "search",
            "alternative", "replacement", "substitute", "instead of", "better than",
            "tutorial", "guide", "instructions", "step by step", "walkthrough",
            "best way", "most effective", "proven method", "tips", "tricks", "hacks"
        ],
        "Money Talk": [
            "price", "cost", "expensive", "cheap", "budget", "affordable", "money", "pay",
            "subscription", "monthly", "yearly", "fee", "charge", "billing", "invoice",
            "worth it", "value", "roi", "return on investment", "save money", "deal",
            "discount", "coupon", "promo", "sale", "free", "pricing", "quote", "estimate",
            "$", "usd", "euro", "pound", "currency", "salary", "income", "revenue", "profit"
        ],
        "Hot Discussions": [
            "trending", "viral", "popular", "everyone", "talking about", "buzz", "hype",
            "news", "announcement", "update", "release", "launch", "breaking", "controversy",
            "debate", "argument", "discussion", "thoughts", "opinions", "what do you think",
            "hot take", "unpopular opinion", "controversial", "drama", "gossip"
        ],
        "Seeking Alternatives": [
            "alternative", "replacement", "substitute", "instead of", "better than", "similar to",
            "like", "competitor", "switch from", "migrate", "move away", "leave", "quit",
            "fed up", "done with", "tired of", "sick of", "switching", "changing",
            "compare", "vs", "versus", "difference", "which is better", "pros and cons"
        ]
    }

def classify_post_content(title: str, text: str) -> Tuple[str, float]:
    """
    Classify a post into one of the GummySearch categories.
    Returns (category, confidence_score).
    """
    keywords = get_category_keywords()
    content = f"{title.lower()} {text.lower()}"
    
    # Remove common noise words for better classification
    noise_words = ["the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by"]
    for word in noise_words:
        content = re.sub(rf'\b{word}\b', '', content)
    
    category_scores = {}
    
    for category, category_keywords in keywords.items():
        score = 0
        for keyword in category_keywords:
            # Use regex for better matching
            pattern = rf'\b{re.escape(keyword.lower())}\b'
            matches = len(re.findall(pattern, content))
            score += matches
            
            # Boost score for title matches (more important)
            title_matches = len(re.findall(pattern, title.lower()))
            score += title_matches * 2
        
        category_scores[category] = score
    
    # Get the category with highest score
    if max(category_scores.values()) == 0:
        return "General Discussion", 0.0
    
    best_category = max(category_scores, key=category_scores.get)
    max_score = category_scores[best_category]
    
    # Calculate confidence (normalize by content length and keyword count)
    total_words = len(content.split())
    confidence = min(max_score / max(total_words * 0.1, 1), 1.0)
    
    return best_category, confidence

def get_category_color(category: str) -> str:
    """Get color for category badges like GummySearch."""
    colors = {
        "Pain Points": "#FF4B4B",      # Red
        "Solution Requests": "#00D4FF", # Blue  
        "Money Talk": "#00FF88",       # Green
        "Hot Discussions": "#FF8C00",  # Orange
        "Seeking Alternatives": "#9966FF", # Purple
        "General Discussion": "#666666"  # Gray
    }
    return colors.get(category, "#666666")

def get_category_icon(category: str) -> str:
    """Get emoji icon for each category."""
    icons = {
        "Pain Points": "😣",
        "Solution Requests": "❓", 
        "Money Talk": "💰",
        "Hot Discussions": "🔥",
        "Seeking Alternatives": "🔄",
        "General Discussion": "💬"
    }
    return icons.get(category, "💬")


# ─────────────────────── helpers: fetch posts & single thread ──────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_subreddit_posts(
    _reddit: praw.Reddit,
    name: str,
    filter_type: str = "All",
    start: date | None = None,
    end:   date | None = None,
    sort_type: str = "New",
) -> pd.DataFrame:
    """
    Scrape posts that fall inside the requested window using the chosen sort.
    • "All", "Last Week", "Last Month", "Last Year" use rolling windows
    • "Date Range" honours the explicit `start` → `end` span
    • Hot/Rising are not chronological so date filtering uses continue, not break.
    Note: Reddit's API caps results at ~1 000 posts per listing.
    """
    try:
        sub   = _reddit.subreddit(name)
        now   = datetime.now(timezone.utc)
        start_ts, end_ts = 0, now.timestamp()

        if filter_type == "Last Week":
            start_ts = (now - timedelta(days=7)).timestamp()
        elif filter_type == "Last Month":
            start_ts = (now - timedelta(days=30)).timestamp()
        elif filter_type == "Last Year":
            start_ts = (now - timedelta(days=365)).timestamp()
        elif filter_type == "Date Range" and start and end:
            start_ts = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
            end_ts   = datetime.combine(end,   datetime.max.time(), tzinfo=timezone.utc).timestamp()

        top_time_map = {"Last Week": "week", "Last Month": "month", "Last Year": "year"}
        if sort_type == "Hot":
            listing = sub.hot(limit=None)
        elif sort_type == "Top":
            listing = sub.top(time_filter=top_time_map.get(filter_type, "all"), limit=None)
        elif sort_type == "Rising":
            listing = sub.rising(limit=None)
        else:
            listing = sub.new(limit=None)

        chronological = sort_type == "New"

        rows: list[dict] = []
        for post in listing:
            if post.created_utc > end_ts:
                continue
            if post.created_utc < start_ts:
                if chronological:
                    break          # new listing is oldest-last, safe to stop early
                continue           # hot/rising are unordered, must keep scanning

            # Classify the post content
            category, confidence = classify_post_content(post.title, post.selftext or "")
            
            rows.append({
                "ID":                  post.id,
                "Title":               post.title,
                "Post Text":           post.selftext,
                "Subreddit":           post.subreddit.display_name,
                "Author":              str(post.author),
                "Created UTC":         datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                "Score":               post.score,
                "Up-vote Ratio":       post.upvote_ratio,
                "Total Comments":      post.num_comments,
                "Total Awards":        post.total_awards_received,
                "Flair":               post.link_flair_text,
                "Is Original Content": post.is_original_content,
                "Over 18":             post.over_18,
                "Spoiler":             post.spoiler,
                "Num Cross-posts":     post.num_crossposts,
                "Permalink":           f"https://www.reddit.com{post.permalink}",
                "Post URL":            post.url,
                "Category":            category,
                "Category Confidence": confidence,
            })
        return pd.DataFrame(rows)

    except Exception as exc:
        st.error(f"Error fetching subreddit posts: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def get_post_by_url(_reddit: praw.Reddit, url: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return a DataFrame for the submission + its **entire** comment tree."""
    try:
        s = _reddit.submission(url=url)
        
        # Classify the post content
        category, confidence = classify_post_content(s.title, s.selftext or "")
        
        post_df = pd.DataFrame([{
            "ID":            s.id,
            "Title":         s.title,
            "Post Text":     s.selftext,
            "Subreddit":     s.subreddit.display_name,
            "Author":        str(s.author),
            "Created UTC":   datetime.fromtimestamp(s.created_utc, tz=timezone.utc),
            "Score":         s.score,
            "Up-vote Ratio": s.upvote_ratio,
            "Total Comments": s.num_comments,
            "Total Awards":   s.total_awards_received,
            "Flair":          s.link_flair_text,
            "Is Original Content": s.is_original_content,
            "Over 18":            s.over_18,
            "Spoiler":            s.spoiler,
            "Num Cross-posts":    s.num_crossposts,
            "Permalink":          f"https://www.reddit.com{s.permalink}",
            "Post URL":           s.url,
            "Category":           category,
            "Category Confidence": confidence,
        }])

        s.comments.replace_more(limit=None)
        comments = [{
            "Comment ID":   c.id,
            "Parent ID":    c.parent_id,
            "Comment Text": c.body,
            "Author":       str(c.author),
            "Score":        c.score,
            "Created UTC":  datetime.fromtimestamp(c.created_utc, tz=timezone.utc),
            "Permalink":    f"https://www.reddit.com{c.permalink}",
            "Is Submitter": c.is_submitter,
        } for c in s.comments.list()]

        return post_df, pd.DataFrame(comments)

    except Exception as exc:
        st.error(f"Error fetching post: {exc}")
        return pd.DataFrame(), pd.DataFrame()


# ──────────────────────────────── Streamlit UI ────────────────────────────────
def apply_custom_css():
    """Apply custom CSS for modern dark theme and better styling."""
    st.markdown("""
    <style>
    /* Import Google Fonts */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    /* Main theme colors */
    :root {
        --primary-color: #FF4B4B;
        --secondary-color: #FF6B6B;
        --background-dark: #0E1117;
        --surface-dark: #1A1D23;
        --surface-light: #262730;
        --text-primary: #FAFAFA;
        --text-secondary: #A6A6A6;
        --accent-blue: #00D4FF;
        --accent-green: #00FF88;
        --border-color: #333644;
    }
    
    /* Main container styling */
    .stApp {
        background: linear-gradient(135deg, var(--background-dark) 0%, #1a1d29 100%);
        font-family: 'Inter', sans-serif;
    }
    
    /* Sidebar styling */
    .css-1d391kg {
        background: linear-gradient(180deg, var(--surface-dark) 0%, var(--surface-light) 100%);
        border-right: 1px solid var(--border-color);
    }
    
    /* Headers and titles */
    .main-header {
        background: linear-gradient(90deg, var(--primary-color), var(--secondary-color));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 700;
        display: inline-block;
        vertical-align: middle;
    }
    
    .section-header {
        color: var(--text-primary);
        font-size: 1.8rem;
        font-weight: 600;
        margin: 1.5rem 0 1rem 0;
        border-bottom: 2px solid var(--accent-blue);
        padding-bottom: 0.5rem;
    }
    
    /* Cards and containers */
    .filter-card {
        background: var(--surface-dark);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    }
    
    .stats-card {
        background: linear-gradient(135deg, var(--surface-dark), var(--surface-light));
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
        border: 1px solid var(--border-color);
    }
    
    /* Buttons */
    .stButton > button {
        background: linear-gradient(45deg, var(--primary-color), var(--secondary-color));
        border: none;
        border-radius: 8px;
        color: white;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(255, 75, 75, 0.3);
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(255, 75, 75, 0.4);
    }
    
    /* Metrics */
    .metric-container {
        background: var(--surface-dark);
        border-radius: 8px;
        padding: 1rem;
        border-left: 4px solid var(--accent-blue);
    }
    
    /* Selectbox and inputs */
    .stSelectbox > div > div {
        background: var(--surface-light);
        border: 1px solid var(--border-color);
        border-radius: 6px;
    }
    
    .stTextInput > div > div > input {
        background: var(--surface-light);
        border: 1px solid var(--border-color);
        border-radius: 6px;
        color: var(--text-primary);
    }
    
    /* Success/Error messages */
    .stSuccess {
        background: linear-gradient(90deg, var(--accent-green), #00cc77);
        border-radius: 8px;
    }
    
    .stError {
        background: linear-gradient(90deg, #ff4757, #ff3742);
        border-radius: 8px;
    }
    
    /* DataFrame styling */
    .stDataFrame {
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
    }
    
    /* Advanced filter section */
    .advanced-filters {
        background: var(--surface-dark);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 1.5rem;
        margin-top: 1rem;
    }
    
    .filter-section {
        margin-bottom: 1rem;
        padding: 1rem;
        background: var(--surface-light);
        border-radius: 8px;
        border-left: 3px solid var(--accent-blue);
    }
    </style>
    """, unsafe_allow_html=True)

def create_category_analytics(df: pd.DataFrame):
    """Create category distribution analytics like GummySearch."""
    if df.empty or 'Category' not in df.columns:
        return
    
    st.markdown('<h3 class="section-header">🏷️ Content Categories</h3>', unsafe_allow_html=True)
    
    # Category distribution
    category_counts = df['Category'].value_counts()
    
    # Create category cards
    cols = st.columns(len(category_counts))
    for i, (category, count) in enumerate(category_counts.items()):
        with cols[i % len(cols)]:
            icon = get_category_icon(category)
            color = get_category_color(category)
            percentage = (count / len(df)) * 100
            
            st.markdown(f"""
                <div style="
                    background: linear-gradient(135deg, {color}20, {color}10);
                    border-left: 4px solid {color};
                    border-radius: 8px;
                    padding: 1rem;
                    margin: 0.5rem 0;
                    text-align: center;
                ">
                    <div style="font-size: 2rem; margin-bottom: 0.5rem;">{icon}</div>
                    <div style="font-weight: 600; color: {color};">{category}</div>
                    <div style="font-size: 1.5rem; font-weight: bold; margin: 0.5rem 0;">{count}</div>
                    <div style="font-size: 0.9rem; opacity: 0.8;">{percentage:.1f}%</div>
                </div>
            """, unsafe_allow_html=True)
    
    # Category distribution chart
    fig_categories = px.pie(
        values=category_counts.values,
        names=category_counts.index,
        title="Category Distribution",
        color=category_counts.index,
        color_discrete_map={
            cat: get_category_color(cat) for cat in category_counts.index
        }
    )
    fig_categories.update_layout(
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        font_color='white'
    )
    st.plotly_chart(fig_categories, use_container_width=True)

def create_stats_dashboard(df: pd.DataFrame):
    """Create a stats dashboard with key metrics."""
    if df.empty:
        return
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("📊 Total Posts", len(df))
    with col2:
        avg_score = df['Score'].mean() if 'Score' in df.columns else 0
        st.metric("⭐ Avg Score", f"{avg_score:.1f}")
    with col3:
        avg_comments = df['Total Comments'].mean() if 'Total Comments' in df.columns else 0
        st.metric("💬 Avg Comments", f"{avg_comments:.1f}")
    with col4:
        total_awards = df['Total Awards'].sum() if 'Total Awards' in df.columns else 0
        st.metric("🏆 Total Awards", int(total_awards))

def main() -> None:
    st.set_page_config(
        page_title="Reddit Data Scraper",
        page_icon="reddit-logo.png",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    apply_custom_css()
    
    if not check_password():
        st.stop()
        
    # Main header with custom logo inline
    st.markdown(
        '''
        <div style="display: flex; align-items: center; justify-content: center; margin-bottom: 2rem;">
            <img src="data:image/png;base64,{}" width="50" style="margin-right: 15px;">
            <h1 class="main-header" style="margin: 0;">Reddit Data Scraper</h1>
        </div>
        '''.format(
            __import__('base64').b64encode(open('reddit-logo.png', 'rb').read()).decode()
        ),
        unsafe_allow_html=True
    )
    
    reddit = init_reddit()

    # Enhanced sidebar with better organization
    with st.sidebar:
        st.markdown("### ⚙️ Configuration")
        
        # Mode selection with better styling
        mode = st.radio(
            "**Scraping Mode**",
            ("Subreddit Posts", "Specific Post by URL"),
            help="Choose what type of data you want to scrape"
        )
        
        st.markdown("---")
        
        # Advanced options
        with st.expander("🔧 Advanced Options"):
            show_charts = st.checkbox("Show Analytics Charts", value=True)

    # ── Subreddit mode ─────────────────────────────────────────────────────────
    if mode == "Subreddit Posts":
        st.markdown('<h2 class="section-header">🔍 Subreddit Posts Scraper</h2>', unsafe_allow_html=True)

        # Main input section with better layout
        col1, col2 = st.columns([2, 1])
        with col1:
            sub_name = st.text_input(
                "**Subreddit Name**",
                value="",
                placeholder="e.g., selfhosted, programming, technology",
                help="Enter the name of the subreddit without 'r/'"
            )
        with col2:
            max_posts = st.number_input(
                "**Max Posts**",
                min_value=10,
                max_value=1000,
                value=100,
                step=10,
                help="Maximum number of posts to fetch"
            )

        # Enhanced filter section
        st.markdown("**📅 Date & Time Filters**")
        
        col1, col2 = st.columns(2)
        with col1:
            filter_opt = st.selectbox(
                "Time Period",
                ("All", "Last Week", "Last Month", "Last Year", "Date Range"),
                help="Select the time period for post filtering"
            )
        with col2:
            sort_option = st.selectbox(
                "Sort By",
                ("New", "Hot", "Top", "Rising"),
                help="Choose how posts should be sorted"
            )

        start_d = end_d = None
        if filter_opt == "Date Range":
            col1, col2 = st.columns(2)
            with col1:
                start_d = st.date_input("Start date", value=date.today() - timedelta(days=7))
            with col2:
                end_d = st.date_input("End date", value=date.today())
            if start_d > end_d:
                st.warning("⚠️ Start date must be before end date.")

        # Content filters
        with st.expander("🎯 Content Filters"):
            col1, col2, col3 = st.columns(3)
            with col1:
                min_score = st.number_input("Min Score", value=0, help="Minimum post score")
                include_nsfw = st.checkbox("Include NSFW", value=False)
            with col2:
                min_comments = st.number_input("Min Comments", value=0, help="Minimum comment count")
                include_spoilers = st.checkbox("Include Spoilers", value=True)
            with col3:
                min_awards = st.number_input("Min Awards", value=0, help="Minimum award count")
                oc_only = st.checkbox("Original Content Only", value=False, help="Only show posts explicitly tagged OC by their author. Rare on most subreddits.")
        
        # Category filters (like GummySearch)
        with st.expander("🏷️ Category Filters (GummySearch Style)"):
            st.markdown("**Filter by Content Categories:**")
            categories = ["All Categories", "Pain Points", "Solution Requests", "Money Talk", "Hot Discussions", "Seeking Alternatives", "General Discussion"]
            
            col1, col2 = st.columns(2)
            with col1:
                selected_categories = st.multiselect(
                    "Select Categories",
                    categories[1:],  # Exclude "All Categories" from multiselect
                    default=[],
                    help="Choose which types of content to include"
                )
            with col2:
                min_confidence = st.slider(
                    "Category Confidence",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.1,
                    step=0.1,
                    help="Minimum confidence for category classification"
                )

        # Session state for persisting results across reruns
        if 'sub_results' not in st.session_state:
            st.session_state.sub_results = None

        # Action button with better styling
        if st.button("🚀 Start Scraping", use_container_width=True):
            with st.spinner("🔍 Collecting posts from r/{} ...".format(sub_name)):
                df = get_subreddit_posts(
                    reddit, sub_name,
                    filter_type=filter_opt,
                    start=start_d, end=end_d,
                    sort_type=sort_option,
                )

            if not df.empty:
                raw_count = len(df)

                # Apply content filters
                if min_score > 0:
                    df = df[df['Score'] >= min_score]
                if min_comments > 0:
                    df = df[df['Total Comments'] >= min_comments]
                if min_awards > 0:
                    df = df[df['Total Awards'] >= min_awards]
                if not include_nsfw:
                    df = df[~df['Over 18']]
                if not include_spoilers:
                    df = df[~df['Spoiler']]
                if oc_only:
                    df = df[df['Is Original Content']]

                # Apply category filters (GummySearch style)
                if selected_categories:
                    df = df[df['Category'].isin(selected_categories)]
                if min_confidence > 0:
                    df = df[df['Category Confidence'] >= min_confidence]

                df = df.head(max_posts)

                st.session_state.sub_results = {'df': df, 'name': sub_name, 'raw_count': raw_count}
            else:
                st.session_state.sub_results = {'df': pd.DataFrame(), 'name': sub_name, 'raw_count': 0}

        # Render results from session state so widget interactions don't clear them
        if st.session_state.sub_results is not None:
            _df = st.session_state.sub_results['df']
            _name = st.session_state.sub_results['name']

            raw_count = st.session_state.sub_results.get('raw_count', len(_df))

            if not _df.empty:
                filtered = raw_count - len(_df)
                suffix = f" ({filtered} removed by filters)" if filtered else ""
                st.success(f"✅ Successfully fetched {len(_df)} posts from r/{_name}{suffix}")

                # Stats dashboard
                create_stats_dashboard(_df)

                # Category analytics (GummySearch style)
                create_category_analytics(_df)

                # Charts section
                if show_charts and len(_df) > 0:
                    st.markdown('<h3 class="section-header">📊 Analytics</h3>', unsafe_allow_html=True)

                    chart_col1, chart_col2 = st.columns(2)

                    with chart_col1:
                        fig_score = px.histogram(
                            _df, x='Score', nbins=20,
                            title="Score Distribution",
                            color_discrete_sequence=['#FF4B4B']
                        )
                        fig_score.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)',
                            font_color='white'
                        )
                        st.plotly_chart(fig_score, use_container_width=True)

                    with chart_col2:
                        day_counts = pd.to_datetime(_df['Created UTC']).dt.date.value_counts().sort_index()
                        posts_per_day = day_counts.reset_index()
                        posts_per_day.columns = ['Date', 'Posts']
                        fig_time = px.line(
                            posts_per_day, x='Date', y='Posts',
                            title="Posts Over Time",
                            color_discrete_sequence=['#00D4FF']
                        )
                        fig_time.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)',
                            font_color='white'
                        )
                        st.plotly_chart(fig_time, use_container_width=True)

                # Data table with enhanced display
                st.markdown('<h3 class="section-header">📋 Post Data</h3>', unsafe_allow_html=True)

                # Column selection — lives outside button block so changes don't clear results
                with st.expander("🔧 Customize Columns"):
                    all_columns = _df.columns.tolist()
                    default_columns = ['Title', 'Category', 'Author', 'Score', 'Total Comments', 'Created UTC', 'Permalink']
                    selected_columns = st.multiselect(
                        "Select columns to display:",
                        all_columns,
                        default=[col for col in default_columns if col in all_columns]
                    )

                display_df = _df[selected_columns] if selected_columns else _df

                # Enhanced dataframe display with category styling
                if 'Category' in display_df.columns:
                    styled_df = display_df.copy()

                    def style_category(val):
                        color = get_category_color(val)
                        return f"background-color: {color}20; color: {color}; font-weight: bold;"

                    styled_df = styled_df.style.map(style_category, subset=['Category'])
                    st.dataframe(styled_df, use_container_width=True, height=400)
                else:
                    st.dataframe(display_df, use_container_width=True, height=400)

                # Download section
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.download_button(
                        "📥 Download Full CSV",
                        _df.to_csv(index=False).encode('utf-8-sig'),
                        f"{_name}_posts_full.csv",
                        "text/csv",
                        use_container_width=True
                    )
                with col2:
                    if selected_columns:
                        st.download_button(
                            "📥 Download Selected CSV",
                            display_df.to_csv(index=False).encode('utf-8-sig'),
                            f"{_name}_posts_selected.csv",
                            "text/csv",
                            use_container_width=True
                        )
                with col3:
                    st.download_button(
                        "📥 Download JSON",
                        _df.to_json(orient='records', date_format='iso').encode(),
                        f"{_name}_posts.json",
                        "application/json",
                        use_container_width=True
                    )
            else:
                if raw_count > 0:
                    st.warning(
                        f"⚠️ Scraped {raw_count} posts from r/{_name} but all were removed by your filters. "
                        "Note: NSFW, Spoiler, and Original Content flags are set explicitly by Reddit users — "
                        "most posts on typical subreddits have none of these set."
                    )
                else:
                    st.error("❌ No posts found. Please check the subreddit name and try again.")

    # ── Single-thread mode ─────────────────────────────────────────────────────
    else:
        st.markdown('<h2 class="section-header">🔗 Post URL Scraper</h2>', unsafe_allow_html=True)

        # URL input with validation
        url = st.text_input(
            "**Reddit Post URL**",
            placeholder="https://www.reddit.com/r/subreddit/comments/post_id/title/",
            help="Enter the full URL of the Reddit post you want to scrape"
        )
        
        # Comment analysis options
        with st.expander("🔧 Comment Analysis Options"):
            col1, col2 = st.columns(2)
            with col1:
                include_deleted = st.checkbox("Include Deleted Comments", value=False)
                sort_comments = st.selectbox("Sort Comments By", ["Score", "Date", "Author"])
            with col2:
                min_comment_score = st.number_input("Min Comment Score", value=-1000)
                max_comments = st.number_input("Max Comments", value=1000, min_value=1)

        # Session state for persisting results across reruns
        if 'url_results' not in st.session_state:
            st.session_state.url_results = None

        if st.button("🚀 Scrape Post & Comments", use_container_width=True):
            if url:
                with st.spinner("📥 Fetching submission & comments..."):
                    post_df, cmt_df = get_post_by_url(reddit, url)

                if not post_df.empty:
                    st.session_state.url_results = {'post_df': post_df, 'cmt_df': cmt_df}
                else:
                    st.session_state.url_results = None
                    st.error("❌ Failed to fetch post data. Please check the URL and try again.")
            else:
                st.warning("⚠️ Please enter a valid Reddit post URL.")

        # Render results from session state so widget interactions don't clear them
        if st.session_state.url_results is not None:
            post_df = st.session_state.url_results['post_df']
            cmt_df  = st.session_state.url_results['cmt_df']

            # Post details section
            st.markdown('<h3 class="section-header">📄 Post Details</h3>', unsafe_allow_html=True)

            if len(post_df) > 0:
                post = post_df.iloc[0]
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("👍 Score", int(post['Score']))
                with col2:
                    st.metric("💬 Comments", int(post['Total Comments']))
                with col3:
                    st.metric("🏆 Awards", int(post['Total Awards']))
                with col4:
                    ratio = post['Up-vote Ratio']
                    st.metric("📈 Upvote Ratio", f"{ratio:.1%}")

            st.dataframe(post_df, use_container_width=True)

            # Comments section
            if not cmt_df.empty:
                filtered_cmt_df = cmt_df.copy()

                if min_comment_score > -1000:
                    filtered_cmt_df = filtered_cmt_df[filtered_cmt_df['Score'] >= min_comment_score]

                if not include_deleted:
                    filtered_cmt_df = filtered_cmt_df[
                        ~filtered_cmt_df['Comment Text'].isin(['[deleted]', '[removed]'])
                    ]

                if sort_comments == "Score":
                    filtered_cmt_df = filtered_cmt_df.sort_values('Score', ascending=False)
                elif sort_comments == "Date":
                    filtered_cmt_df = filtered_cmt_df.sort_values('Created UTC', ascending=False)
                elif sort_comments == "Author":
                    filtered_cmt_df = filtered_cmt_df.sort_values('Author')

                if len(filtered_cmt_df) > max_comments:
                    filtered_cmt_df = filtered_cmt_df.head(max_comments)

                st.markdown(f'<h3 class="section-header">💬 Comments ({len(filtered_cmt_df)} of {len(cmt_df)})</h3>', unsafe_allow_html=True)

                if show_charts and len(filtered_cmt_df) > 0:
                    chart_col1, chart_col2 = st.columns(2)

                    with chart_col1:
                        fig_comments = px.histogram(
                            filtered_cmt_df, x='Score', nbins=20,
                            title="Comment Score Distribution",
                            color_discrete_sequence=['#00D4FF']
                        )
                        fig_comments.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)',
                            font_color='white'
                        )
                        st.plotly_chart(fig_comments, use_container_width=True)

                    with chart_col2:
                        filtered_cmt_df['Date'] = pd.to_datetime(filtered_cmt_df['Created UTC']).dt.date
                        comments_per_day = filtered_cmt_df.groupby('Date').size().reset_index(name='Comments')
                        fig_cmt_time = px.line(
                            comments_per_day, x='Date', y='Comments',
                            title="Comments Over Time",
                            color_discrete_sequence=['#00FF88']
                        )
                        fig_cmt_time.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)',
                            font_color='white'
                        )
                        st.plotly_chart(fig_cmt_time, use_container_width=True)

                st.dataframe(filtered_cmt_df, use_container_width=True, height=400)
            else:
                st.info("No comments found for this post.")

            # Download section
            st.markdown('<h3 class="section-header">📥 Download Options</h3>', unsafe_allow_html=True)
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.download_button(
                    "📄 Post CSV",
                    post_df.to_csv(index=False).encode('utf-8-sig'),
                    "post_details.csv",
                    "text/csv",
                    use_container_width=True
                )
            with col2:
                if not cmt_df.empty:
                    st.download_button(
                        "💬 Comments CSV",
                        filtered_cmt_df.to_csv(index=False).encode('utf-8-sig'),
                        "comments.csv",
                        "text/csv",
                        use_container_width=True
                    )
            with col3:
                st.download_button(
                    "📄 Post JSON",
                    post_df.to_json(orient='records', date_format='iso').encode(),
                    "post_details.json",
                    "application/json",
                    use_container_width=True
                )
            with col4:
                if not cmt_df.empty:
                    st.download_button(
                        "💬 Comments JSON",
                        filtered_cmt_df.to_json(orient='records', date_format='iso').encode(),
                        "comments.json",
                        "application/json",
                        use_container_width=True
                    )

if __name__ == "__main__":
    main()
