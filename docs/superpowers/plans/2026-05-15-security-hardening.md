# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Streamlit password gate with a global cross-session `RateLimiter` class, constant-time comparison, submit-only validation, stronger CAPTCHA, and updated docs.

**Architecture:** A `RateLimiter` class (backed by `st.cache_resource` for cross-session singleton) lives alongside `check_password()` in `security.py`. The class owns all counter/lockout state behind a `threading.Lock`; `check_password()` delegates all state management to it and focuses on UI only. `secrets.toml.example` gains an `APP_PASSWORD` entry.

**Tech Stack:** Python stdlib (`hmac`, `threading`, `datetime`), Streamlit 1.48.1 (`st.cache_resource`), pytest

---

### Task 1: Set up tests and write failing tests for RateLimiter

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_rate_limiter.py`

- [ ] **Step 1: Install pytest**

```bash
pip install pytest
```

Expected: pytest installs successfully.

- [ ] **Step 2: Create the tests directory**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 3: Write the test file**

Create `tests/test_rate_limiter.py` with the following content:

```python
import threading
from datetime import datetime, timezone, timedelta

from security import RateLimiter


def test_initial_state():
    limiter = RateLimiter()
    assert not limiter.is_locked()
    assert limiter.seconds_remaining() == 0
    assert limiter.failed_attempts() == 0


def test_failure_increments_counter():
    limiter = RateLimiter()
    limiter.record_failure()
    limiter.record_failure()
    assert limiter.failed_attempts() == 2
    assert not limiter.is_locked()


def test_lockout_does_not_trigger_before_threshold():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD - 1):
        limiter.record_failure()
    assert not limiter.is_locked()


def test_lockout_triggers_at_threshold():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD):
        limiter.record_failure()
    assert limiter.is_locked()
    assert limiter.seconds_remaining() > 0


def test_success_resets_state():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD):
        limiter.record_failure()
    assert limiter.is_locked()
    limiter.record_success()
    assert not limiter.is_locked()
    assert limiter.failed_attempts() == 0
    assert limiter.seconds_remaining() == 0


def test_lockout_auto_clears_after_expiry():
    limiter = RateLimiter()
    for _ in range(RateLimiter.LOCKOUT_THRESHOLD):
        limiter.record_failure()
    # Manually backdate the lockout to simulate expiry
    limiter._locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert not limiter.is_locked()
    assert limiter.seconds_remaining() == 0


def test_concurrent_failures_no_corruption():
    limiter = RateLimiter()
    threads = [threading.Thread(target=limiter.record_failure) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert limiter.failed_attempts() == 20
```

- [ ] **Step 4: Run tests — verify they fail with ImportError**

```bash
pytest tests/test_rate_limiter.py -v
```

Expected: All 7 tests FAIL with `ImportError: cannot import name 'RateLimiter' from 'security'`.

---

### Task 2: Implement RateLimiter in security.py

**Files:**
- Modify: `security.py` (add imports and `RateLimiter` class before `check_password()`)

- [ ] **Step 1: Replace the imports block at the top of security.py**

Current top of `security.py` (lines 1–3):
```python
import os
import random
import streamlit as st
```

Replace with:
```python
import hmac
import os
import random
import threading
from datetime import datetime, timezone, timedelta

import streamlit as st
```

- [ ] **Step 2: Insert the RateLimiter class and factory after the imports, before check_password()**

Insert this block between the imports and `def check_password()`:

```python
@st.cache_resource
def _get_rate_limiter() -> "RateLimiter":
    return RateLimiter()


class RateLimiter:
    CAPTCHA_THRESHOLD = 3
    LOCKOUT_THRESHOLD = 5
    LOCKOUT_DURATION = 60  # seconds

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failed_attempts: int = 0
        self._locked_until: datetime | None = None

    def is_locked(self) -> bool:
        with self._lock:
            if self._locked_until is None:
                return False
            if datetime.now(timezone.utc) >= self._locked_until:
                self._locked_until = None
                return False
            return True

    def seconds_remaining(self) -> int:
        with self._lock:
            if self._locked_until is None:
                return 0
            return max(0, int((self._locked_until - datetime.now(timezone.utc)).total_seconds()))

    def failed_attempts(self) -> int:
        with self._lock:
            return self._failed_attempts

    def record_failure(self) -> None:
        with self._lock:
            self._failed_attempts += 1
            if self._failed_attempts >= self.LOCKOUT_THRESHOLD:
                self._locked_until = datetime.now(timezone.utc) + timedelta(seconds=self.LOCKOUT_DURATION)

    def record_success(self) -> None:
        with self._lock:
            self._failed_attempts = 0
            self._locked_until = None
```

- [ ] **Step 3: Run the tests — verify they pass**

```bash
pytest tests/test_rate_limiter.py -v
```

Expected:
```
tests/test_rate_limiter.py::test_initial_state PASSED
tests/test_rate_limiter.py::test_failure_increments_counter PASSED
tests/test_rate_limiter.py::test_lockout_does_not_trigger_before_threshold PASSED
tests/test_rate_limiter.py::test_lockout_triggers_at_threshold PASSED
tests/test_rate_limiter.py::test_success_resets_state PASSED
tests/test_rate_limiter.py::test_lockout_auto_clears_after_expiry PASSED
tests/test_rate_limiter.py::test_concurrent_failures_no_corruption PASSED

7 passed
```

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/test_rate_limiter.py security.py
git commit -m "feat: add RateLimiter class with global cross-session brute-force protection"
```

---

### Task 3: Rewrite check_password() to use RateLimiter

**Files:**
- Modify: `security.py` (replace entire `check_password()` function, lines 5–86)

- [ ] **Step 1: Replace the entire check_password() function**

Delete everything from `def check_password() -> bool:` to the end of the file and replace with:

```python
def check_password() -> bool:
    """Returns True if the user has provided the correct password."""
    app_password = os.getenv("APP_PASSWORD")
    if not app_password:
        try:
            app_password = st.secrets.get("APP_PASSWORD")
        except Exception:
            app_password = None

    if not app_password:
        return True

    if st.session_state.get("password_correct", False):
        return True

    limiter = _get_rate_limiter()

    st.markdown("""
        <style>
            [data-testid="stHeader"] {visibility: hidden;}
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            [data-testid="InputInstructions"] {display: none !important;}
        </style>
    """, unsafe_allow_html=True)

    if limiter.is_locked():
        secs = limiter.seconds_remaining()
        st.markdown(f'''
            <div style="text-align: center; margin-top: 50px;">
                <div style="font-size: 4rem; margin-bottom: 1rem;">🔒</div>
                <div style="font-size: 1.8rem; font-weight: 700; color: var(--text-primary); margin-bottom: 0.5rem;">Too Many Attempts</div>
                <p style="color: var(--text-secondary); font-size: 1rem;">Please wait <strong>{secs}</strong> second(s) before trying again.</p>
            </div>
        ''', unsafe_allow_html=True)
        return False

    st.markdown('''
        <div style="text-align: center; margin-top: 50px;">
            <div style="font-size: 4rem; margin-bottom: 1rem;">🔐</div>
            <div style="font-size: 1.8rem; font-weight: 700; color: var(--text-primary); margin-bottom: 0.5rem;">Secure Access Required</div>
            <p style="color: var(--text-secondary); font-size: 1rem;">Please enter the password to unlock this application.</p>
        </div>
    ''', unsafe_allow_html=True)

    if "captcha_a" not in st.session_state:
        st.session_state.captcha_a = random.randint(1, 99)
        st.session_state.captcha_b = random.randint(1, 99)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password = st.text_input(
            "Password", type="password", label_visibility="collapsed", placeholder="Enter Password"
        )

        captcha_answer = None
        attempts = limiter.failed_attempts()
        if attempts >= RateLimiter.CAPTCHA_THRESHOLD:
            st.warning("Too many failed attempts. Please solve the CAPTCHA to continue.")
            captcha_answer = st.text_input(
                f"What is {st.session_state.captcha_a} + {st.session_state.captcha_b}?",
                key="captcha",
            )

        submit = st.button("Unlock", use_container_width=True)

        if submit:
            if attempts >= RateLimiter.CAPTCHA_THRESHOLD:
                try:
                    if int(captcha_answer) != (st.session_state.captcha_a + st.session_state.captcha_b):
                        st.error("🤖 Incorrect CAPTCHA. Please try again.")
                        return False
                except (ValueError, TypeError):
                    st.error("🤖 Please enter a valid number for the CAPTCHA.")
                    return False

            if hmac.compare_digest(password, app_password):
                limiter.record_success()
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                limiter.record_failure()
                st.session_state.captcha_a = random.randint(1, 99)
                st.session_state.captcha_b = random.randint(1, 99)
                if limiter.is_locked():
                    st.rerun()
                st.error("😕 Incorrect password. Please try again.")

    return False
```

- [ ] **Step 2: Run the test suite to confirm nothing is broken**

```bash
pytest tests/test_rate_limiter.py -v
```

Expected: 7 passed (same as before — RateLimiter behaviour unchanged).

- [ ] **Step 3: Commit**

```bash
git add security.py
git commit -m "fix: harden check_password — global rate limit, constant-time compare, submit-only validation, wider CAPTCHA range"
```

---

### Task 4: Update secrets.toml.example

**Files:**
- Modify: `.streamlit/secrets.toml.example`

- [ ] **Step 1: Add APP_PASSWORD to the example file**

Current content of `.streamlit/secrets.toml.example`:
```toml
# Streamlit Secrets Configuration Template
# Copy this file to .streamlit/secrets.toml and fill in your actual credentials
# For local development only - DO NOT commit actual credentials to git

# Reddit API Credentials
# Get these from https://www.reddit.com/prefs/apps
REDDIT_CLIENT_ID = "your_reddit_client_id_here"
REDDIT_CLIENT_SECRET = "your_reddit_client_secret_here"
REDDIT_USER_AGENT = "RedditScraper/1.0 by /u/yourusername"

# ⚠️ IMPORTANT SECURITY NOTES:
# 1. Never commit actual credentials to version control
# 2. For Streamlit Cloud deployment, add secrets in the web dashboard
# 3. Keep your credentials secure and rotate them regularly
# 4. Use descriptive user agent strings for Reddit API compliance
```

Replace with:
```toml
# Streamlit Secrets Configuration Template
# Copy this file to .streamlit/secrets.toml and fill in your actual credentials
# For local development only - DO NOT commit actual credentials to git

# Reddit API Credentials
# Get these from https://www.reddit.com/prefs/apps
REDDIT_CLIENT_ID = "your_reddit_client_id_here"
REDDIT_CLIENT_SECRET = "your_reddit_client_secret_here"
REDDIT_USER_AGENT = "RedditScraper/1.0 by /u/yourusername"

# App Password (optional)
# Set this to restrict access to the deployed app.
# If omitted, the app is publicly accessible — anyone with the URL can use it.
# For Streamlit Cloud: add this in Settings > Secrets in the web dashboard.
APP_PASSWORD = "your_strong_password_here"

# ⚠️ IMPORTANT SECURITY NOTES:
# 1. Never commit actual credentials to version control
# 2. For Streamlit Cloud deployment, add secrets in the web dashboard
# 3. Keep your credentials secure and rotate them regularly
# 4. Use descriptive user agent strings for Reddit API compliance
```

- [ ] **Step 2: Commit**

```bash
git add .streamlit/secrets.toml.example
git commit -m "docs: add APP_PASSWORD to secrets.toml.example"
```

---

### Task 5: Squash into a single PR commit

- [ ] **Step 1: Identify the base commit (the one before Task 2's commit)**

```bash
git log --oneline -6
```

Note the hash of the commit just before the RateLimiter commit (should be the design doc commit `docs: add security hardening design spec`).

- [ ] **Step 2: Squash the last 3 commits into one**

```bash
git reset --soft HEAD~3
git commit -m "$(cat <<'EOF'
feat: harden password gate with global RateLimiter and security fixes

- Add RateLimiter class (st.cache_resource singleton) for cross-session
  brute-force protection: CAPTCHA after 3 failures, 60s lockout after 5
- Fix check_password() to only validate on button submit, not every keystroke
- Replace == with hmac.compare_digest() for constant-time password comparison
- Widen CAPTCHA number range from 1-10 to 1-99
- Broaden secrets exception handling from FileNotFoundError to Exception
- Add APP_PASSWORD entry to secrets.toml.example

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Verify the final state**

```bash
git log --oneline -4
pytest tests/test_rate_limiter.py -v
```

Expected: 7 passed. The squashed commit sits cleanly on top of `docs: add security hardening design spec`.

---

### Manual Verification Checklist

After implementation, verify these scenarios by running `streamlit run main.py` locally with `APP_PASSWORD=test123` set in `.env`:

- [ ] Correct password → app unlocks immediately
- [ ] Wrong password × 3 → CAPTCHA math question appears
- [ ] Wrong password × 5 → lockout screen with countdown shown
- [ ] Open a **new browser tab** during lockout → sees lockout immediately (proves global state)
- [ ] Wait for lockout to expire → lock screen reappears, counter reset
- [ ] Correct password after lockout expires → unlocks and resets counter
- [ ] Remove `APP_PASSWORD` from `.env` → app loads without any lock screen
