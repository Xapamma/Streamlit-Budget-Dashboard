"""Sidebar UI for choosing an AI provider, model, and (session-only) API key."""

from __future__ import annotations

import streamlit as st

from ai_providers import PROVIDERS, AIError, AISettings, chat, validate_settings

PROVIDER_KEY = "ai_provider"


def _clear_key(provider: str) -> None:
    st.session_state[f"ai_key_{provider}"] = ""
    st.session_state[f"ai_ack_{provider}"] = False


def render_ai_settings() -> AISettings | None:
    """Render the settings and return ready-to-use settings, or None if incomplete."""
    with st.expander("AI settings (optional)", expanded=False):
        st.caption(
            "AI features are optional. Bring your own provider account: you are responsible for "
            "your usage, billing, quotas, and the provider's terms. Free tiers and model "
            "availability can change."
        )
        provider = st.selectbox(
            "AI provider",
            list(PROVIDERS),
            format_func=lambda name: PROVIDERS[name].label,
            key=PROVIDER_KEY,
        )
        info = PROVIDERS[provider]

        api_key = ""
        if info.needs_key:
            api_key = st.text_input(
                f"{info.label} API key",
                type="password",
                key=f"ai_key_{provider}",
                help="Kept only in this browser session's memory. Never saved to disk or the database.",
            ).strip()
            st.link_button("Get an API key", info.key_url)
            st.button(
                "Clear API key",
                key=f"ai_clear_{provider}",
                on_click=_clear_key,
                args=(provider,),
            )
        else:
            st.link_button("Download Ollama", info.key_url)

        model = st.text_input(
            "Model",
            value=info.default_model,
            key=f"ai_model_{provider}",
            help="Suggested: " + ", ".join(info.suggested_models),
        ).strip()
        st.caption(
            "Suggested models: " + ", ".join(f"`{name}`" for name in info.suggested_models)
            + ". Model names and free-tier status are not verified by this app; "
            f"check [pricing and models]({info.docs_url})."
        )

        st.warning(info.privacy_notice)
        st.markdown(f"[Provider privacy policy]({info.privacy_url})")
        acknowledged = True
        if info.requires_acknowledgement:
            acknowledged = st.checkbox(
                "I understand and want to send my requests to this provider.",
                key=f"ai_ack_{provider}",
            )

        st.caption(
            "Your key is sent to this hosted app to make requests; a password field only hides "
            "it on screen. Keys can remain in server memory until your session ends, so use a "
            "key with a spending limit and revoke it when done."
        )

        settings = AISettings(provider=provider, model=model, api_key=api_key)
        problem = None
        try:
            validate_settings(settings)
        except AIError as error:
            problem = str(error)
        if problem is None and not acknowledged:
            problem = "Confirm the privacy notice to enable AI features."

        if problem:
            st.info(problem)
            return None

        if st.button("Test connection", key=f"ai_test_{provider}"):
            try:
                with st.spinner("Testing..."):
                    chat(settings, [{"role": "user", "content": "Reply with the single word OK."}])
                st.success("Connection works.")
            except AIError as error:
                st.error(str(error))
        return settings
