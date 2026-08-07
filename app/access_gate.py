"""Lightweight, low-tech access gate: type your @quadstrat.com email or the
app won't proceed. This is a deterrent for casual/accidental use of a tool
that can spend real Apify and OpenAI credits -- NOT real access control.
Anyone who reads the source can bypass it trivially. Treat it as a speed
bump, not a security boundary.
"""
import re

ALLOWED_DOMAIN = "quadstrat.com"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_allowed(email):
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return False
    domain = email.rpartition("@")[2]
    return domain == ALLOWED_DOMAIN or domain.endswith(f".{ALLOWED_DOMAIN}")


def require_quadstrat_email(st):
    """Blocks the rest of the app (via st.stop()) until a valid-looking
    @quadstrat.com email has been entered this session."""
    if st.session_state.get("_gate_email_ok"):
        return

    st.title("Owned advertising analysis")
    st.caption("Quadrant Strategies")
    st.info("Enter your email to access this tool.")
    email = st.text_input("Work email", placeholder="your@email.com")
    if st.button("Continue"):
        if _is_allowed(email):
            st.session_state["_gate_email_ok"] = True
            st.rerun()
        else:
            st.error(f"That doesn't look like a work email -- "
                     f"access is limited to our team.")
    st.stop()
