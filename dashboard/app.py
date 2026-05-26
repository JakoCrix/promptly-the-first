# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from db import (
    add_to_decks,
    delete_word,
    get_all_words_df,
    get_word_pool,
    init_word_pool,
    reject_from_pool,
    update_word_entry,
)
from core.config import ALLOWED_CHAT_IDS, RETIREMENT_THRESHOLD

st.set_page_config(page_title="Promptly Dashboard", layout="wide")
init_word_pool()

df = get_all_words_df()

with st.sidebar:
    st.header("Context")
    chat_id_options = sorted(df["chat_id"].unique().tolist()) if not df.empty else []
    topic_options = sorted(df["topic"].unique().tolist()) if not df.empty else []
    selected_chat_id = st.selectbox("Chat ID", options=chat_id_options)
    selected_topic = st.selectbox("Topic", options=topic_options)

tab1, tab2 = st.tabs(["📚 Vocabulary Navigator", "🤖 Word Discovery Pool"])

# ── Tab 1: Vocabulary Navigator ────────────────────────────────────────────────
with tab1:
    st.header("Vocabulary Navigator")

    view = df[df["topic"] == selected_topic] if selected_topic else df

    total = len(view)
    retired = int((view["mastery_score"] >= RETIREMENT_THRESHOLD).sum())
    col_total, col_retired = st.columns(2)
    col_total.metric("Total Words", total)
    col_retired.metric("Retired (mastery ≥ 10)", retired)

    st.divider()

    if view.empty:
        st.info("No words in database yet.")
    else:
        st.dataframe(view.drop(columns=["chat_id"]), use_container_width=True)

        st.subheader("Edit a word")
        word_id_options = view["word_id"].tolist()
        selected_word_id = st.selectbox("Select word to edit", options=word_id_options)

        current = view[view["word_id"] == selected_word_id].iloc[0]
        with st.form("edit_word_form"):
            col_word, col_sentence = st.columns(2)
            with col_word:
                new_word = st.text_input("Word", value=current["word"])
            with col_sentence:
                new_sentence = st.text_area("Sentence", value=current["sentence"])
            submitted = st.form_submit_button("Update")

        if submitted:
            if not new_word.strip() or not new_sentence.strip():
                st.warning("Word and sentence cannot be empty.")
            else:
                rowcount = update_word_entry(
                    selected_chat_id,
                    selected_topic,
                    selected_word_id,
                    new_word.strip(),
                    new_sentence.strip(),
                )
                if rowcount > 0:
                    st.toast(f"'{selected_word_id}' updated ✅")
                    st.rerun()
                else:
                    st.toast("Update failed — please try again ⚠️")

        st.divider()
        confirm_key = f"confirm_delete_{selected_topic}_{selected_word_id}"
        col_del, col_warn, col_yes, col_no = st.columns([2, 4, 1, 1])
        with col_del:
            if st.button("🗑️ Delete this word", type="secondary"):
                st.session_state[confirm_key] = True
        if st.session_state.get(confirm_key):
            col_warn.caption("⚠️ This cannot be undone.")
            with col_yes:
                if st.button("Yes, delete", type="primary"):
                    delete_word(selected_topic, selected_word_id)
                    st.session_state.pop(confirm_key, None)
                    st.toast(f"'{selected_word_id}' deleted 🗑️")
                    st.rerun()
            with col_no:
                if st.button("No"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()

# ── Tab 2: Word Discovery Pool ─────────────────────────────────────────────────
with tab2:
    st.header("Word Discovery Pool")

    if not ALLOWED_CHAT_IDS:
        st.warning("No CHAT_IDS configured in .env — words cannot be added to decks.")

    if st.button("🤖 Seed words (AI)"):
        st.info("AI seeding coming soon")

    st.divider()

    pool = get_word_pool()

    if not pool:
        st.info("No candidate words yet.")
    else:
        st.caption(f"{len(pool)} candidate word(s) in the pool")
        for row in pool:
            with st.container(border=True):
                left, right = st.columns([3, 1])
                with left:
                    st.markdown(f"**{row['word']}** — `{row['word_id']}`")
                    st.caption(f"Topic: {row['topic']}")
                    st.write(row["sentence"])
                with right:
                    if st.button("✅ Add to Decks", key=f"add_{row['id']}"):
                        try:
                            inserted, skipped = add_to_decks(
                                row["id"],
                                row["topic"],
                                row["word_id"],
                                row["word"],
                                row["sentence"],
                            )
                            msg = f"Added '{row['word']}' to {inserted} deck(s)"
                            if skipped:
                                msg += f"; {skipped} already existed"
                            st.toast(msg + " ✅")
                            st.rerun()
                        except Exception as e:
                            st.toast(f"Error adding word: {e} ⚠️")

                    if st.button("🗑️ Reject", key=f"reject_{row['id']}"):
                        reject_from_pool(row["id"])
                        st.toast(f"Rejected '{row['word']}' 🗑️")
                        st.rerun()
