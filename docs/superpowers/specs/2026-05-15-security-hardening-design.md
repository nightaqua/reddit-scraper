# Security Hardening Design — Password Gate

**Date:** 2026-05-15  
**Scope:** `security.py`, `.streamlit/secrets.toml.example`  
**PR type:** Security patch / enhancement

---

## Problem

The existing `check_password()` implementation (added in the last commit) protects the app behind `APP_PASSWORD` but has five exploitable weaknesses:

1. **Per-session brute-force counter** — opening a new browser tab resets `failed_attempts` to zero, bypassing the CAPTCHA entirely.
2. **Keystroke-triggered validation** — `if submit or password:` fires on every character typed, incrementing the failure counter for partial passwords and leaking timing information.
3. **Timing-vulnerable comparison** — `password == app_password` is not constant-time and is susceptible to timing side-channel attacks.
4. **Trivially weak CAPTCHA** — numbers in range 1–10 produce only 20 distinct sums; a script can brute-force all answers instantly.
5. **Missing docs** — `APP_PASSWORD` is not mentioned in `secrets.toml.example`, making the feature non-obvious to operators.

---

## Approach

**Approach B — Extract a `RateLimiter` class** (chosen over in-place hardening for cleaner separation of concerns at minimal extra cost).

All changes are confined to `security.py` and `secrets.toml.example`. `main.py` is unchanged.

---

## Architecture

### `RateLimiter`

A class instantiated **once** via `st.cache_resource`, making it a singleton shared across all active Streamlit sessions on the same server instance. Owns all counter/lockout state and a `threading.Lock` for safe concurrent writes.

**State:**
- `_failed_attempts: int` — global count of failed password attempts since last reset
- `_locked_until: datetime | None` — UTC timestamp when the current lockout expires; `None` if not locked

**Interface:**
```python
is_locked() -> bool
seconds_remaining() -> int
record_failure() -> None   # increments counter; triggers 60s lockout at 5 failures
record_success() -> None   # resets counter and clears lockout
```

The `threading.Lock` ensures that two sessions submitting simultaneously cannot both read `failed_attempts = 4` and both trigger a lockout race.

### `check_password() -> bool`

Unchanged signature — still the single entry point called from `main.py`. Delegates all state management to `RateLimiter` and focuses on UI rendering and flow control only.

---

## Data Flow

```
check_password() called
  │
  ├─ app_password absent? → return True  (dev bypass)
  ├─ session_state.password_correct? → return True
  │
  ├─ RateLimiter.is_locked()?
  │     └─ yes → show lockout UI with countdown → return False
  │
  ├─ Render lock screen UI
  ├─ If failed_attempts >= 3 → show CAPTCHA (range 1–99)
  ├─ Render "Unlock" button
  │
  └─ if submit:  ← only on button click, not on keystroke
        ├─ CAPTCHA required? → validate; error + return False on failure
        ├─ hmac.compare_digest(password, app_password)?
        │     ├─ correct → RateLimiter.record_success(), set session_state, rerun
        │     └─ wrong  → RateLimiter.record_failure()
        │                   ├─ attempts >= 5 → lockout set, rerun
        │                   └─ attempts 3–4 → reroll CAPTCHA, show error
        └─ return False
```

---

## Key Behaviour Changes

| Behaviour | Before | After |
|-----------|--------|-------|
| Brute-force counter scope | Per session | Global (all sessions) |
| Password check trigger | Every keystroke + submit | Submit only |
| Password comparison | `==` (timing-vulnerable) | `hmac.compare_digest()` |
| CAPTCHA number range | 1–10 (20 distinct sums) | 1–99 (~9800 distinct sums) |
| Hard lockout | None | 5 failures → 60s lockout |
| CAPTCHA speedbump threshold | 3 failures | 3 failures (unchanged) |

---

## Error Handling

- **Secrets misconfigured** — `except FileNotFoundError` replaced with `except Exception` so `KeyError` / `AttributeError` from partial secrets config is also caught; treated as "no password set."
- **CAPTCHA empty on submit** — existing `try/except (ValueError, TypeError)` covers this; no change needed.
- **Concurrent writes** — `threading.Lock` inside `RateLimiter` serialises counter updates.
- **Server restart** — `st.cache_resource` is in-process memory; restart clears rate-limit state. Acceptable for a stateless single-instance deployment.
- **No password configured** — `check_password()` returns `True` before `RateLimiter` is instantiated; zero overhead for dev environments.

---

## Files Changed

| File | Change |
|------|--------|
| `security.py` | Add `RateLimiter` class; rewrite `check_password()` logic |
| `.streamlit/secrets.toml.example` | Add `APP_PASSWORD` entry with usage comment |

---

## Testing

### Automated (RateLimiter only — no Streamlit dependency)

- `record_failure()` × 5 → `is_locked()` is `True`, `seconds_remaining()` > 0
- `record_success()` after failures → counter resets, `is_locked()` is `False`
- Concurrent `record_failure()` calls via threads → no counter corruption

### Manual checklist

- [ ] Correct password → unlocks immediately
- [ ] Wrong password × 3 → CAPTCHA appears
- [ ] Wrong password × 5 → lockout message with countdown shown
- [ ] New browser tab during lockout → sees lockout immediately (proves global state)
- [ ] Correct password after lockout expires → unlocks and resets counter
- [ ] No `APP_PASSWORD` set → app loads without lock screen
