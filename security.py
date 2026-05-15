import hmac
import os
import random
import threading
from datetime import datetime, timezone, timedelta

import streamlit as st


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
