# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st
from db import (
    bulk_delete_words,
    bulk_delta_mastery,
    bulk_set_mastery,
    get_users,
    get_words_for_user,
    update_mastery,
    update_word_corpus,
)
from core.config import RETIREMENT_THRESHOLD

st.set_page_config(page_title="Promptly Dashboard", layout="wide")


@st.cache_data(ttl=30)
def _load_words(chat_id: int, topic: str | None) -> pd.DataFrame:
    return get_words_for_user(chat_id, topic)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Promptly")
    st.caption("Vocab learning bot dashboard")
    st.divider()

    users = get_users()
    if not users:
        st.warning("No users found in the database.")
        st.stop()

    selected_user = st.selectbox("User (chat_id)", options=users, key="selected_user")

    # Clear stale editor state when the user changes
    if st.session_state.get("_last_user") != selected_user:
        st.session_state.pop("mastery_editor", None)
        st.session_state.pop("selected_ids", None)
        st.session_state["_last_user"] = selected_user

    all_df = get_words_for_user(selected_user)
    topic_list = sorted(all_df["topic"].unique().tolist()) if not all_df.empty else []
    topic_options = ["All topics"] + topic_list

    selected_topic = st.selectbox("Topic filter", options=topic_options, key="selected_topic")

@st.fragment
def _mastery_tab(selected_user: int, selected_topic: str) -> None:
    topic_arg = None if selected_topic == "All topics" else selected_topic
    df = _load_words(selected_user, topic_arg)

    if df.empty:
        st.info("No words for this user / topic combination.")
        return

    # Stats row
    never_seen = int(df["last_seen"].isna().sum())
    retired = int((df["mastery_score"] >= RETIREMENT_THRESHOLD).sum())
    avg_mastery = round(float(df["mastery_score"].mean()), 1)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total words", len(df))
    c2.metric("Never seen", never_seen)
    c3.metric(f"Retired (≥{RETIREMENT_THRESHOLD})", retired)
    c4.metric("Avg mastery", avg_mastery)

    st.divider()

    def _status(row):
        if row["mastery_score"] >= RETIREMENT_THRESHOLD:
            return "Retired"
        if pd.isna(row["last_seen"]):
            return "New"
        return "Active"

    display_df = df.copy()
    display_df["status"] = display_df.apply(_status, axis=1)

    # Quick-select buttons
    qcol1, qcol2, qcol3, qcol4 = st.columns([1, 1, 1, 1])
    with qcol1:
        if st.button("Select all", key="qs_all"):
            st.session_state["selected_ids"] = set(display_df["word_id"])
            st.session_state.pop("mastery_editor", None)
    with qcol2:
        if st.button("Deselect all", key="qs_none"):
            st.session_state["selected_ids"] = set()
            st.session_state.pop("mastery_editor", None)
    with qcol3:
        if st.button("Select New", key="qs_new"):
            st.session_state["selected_ids"] = set(display_df[display_df["status"] == "New"]["word_id"])
            st.session_state.pop("mastery_editor", None)
    with qcol4:
        if st.button("Select Retired", key="qs_retired"):
            st.session_state["selected_ids"] = set(display_df[display_df["status"] == "Retired"]["word_id"])
            st.session_state.pop("mastery_editor", None)

    # Read AFTER buttons so this run's handler updates are visible
    selected_ids: set = st.session_state.get("selected_ids", set())
    display_df.insert(0, "_select", display_df["word_id"].isin(selected_ids))

    col_config = {
        "_select":       st.column_config.CheckboxColumn("Select"),
        "topic":         st.column_config.TextColumn("Topic",    disabled=True),
        "word_id":       st.column_config.TextColumn("Word ID",  disabled=True),
        "word":          st.column_config.TextColumn("Word",     disabled=True),
        "hint":          st.column_config.TextColumn("Hint",     disabled=True),
        "mastery_score": st.column_config.NumberColumn("Mastery", min_value=0, step=1),
        "last_seen":     st.column_config.TextColumn("Last seen", disabled=True),
        "status":        st.column_config.TextColumn("Status",   disabled=True),
    }

    edited_df = st.data_editor(
        display_df,
        column_config=col_config,
        use_container_width=True,
        num_rows="fixed",
        key="mastery_editor",
    )

    # Sync selections back to session state so they survive reruns
    st.session_state["selected_ids"] = set(edited_df[edited_df["_select"] == True]["word_id"])

    # Inline mastery save
    changed_mask = edited_df["mastery_score"] != display_df["mastery_score"]
    changed_rows = edited_df[changed_mask]
    n_changed = int(len(changed_rows))

    if n_changed > 0:
        if st.button(f"Save {n_changed} change(s)", type="primary", key="save_inline"):
            for _, row in changed_rows.iterrows():
                update_mastery(selected_user, row["topic"], row["word_id"], int(row["mastery_score"]))
            st.session_state.pop("mastery_editor", None)
            _load_words.clear()
            st.toast(f"Saved {n_changed} mastery update(s)")

    # Bulk actions
    selected_rows_df = edited_df[edited_df["_select"] == True]
    n_selected = len(selected_rows_df)

    st.divider()
    st.subheader(f"Bulk actions — {n_selected} row(s) selected")

    if n_selected == 0:
        st.caption("Select rows in the table above to enable bulk actions.")
    else:
        selected_tuples = list(zip(selected_rows_df["topic"], selected_rows_df["word_id"]))

        bcol1, bcol2, bcol3, bcol4, bcol5 = st.columns([2, 1, 1, 1, 1])

        with bcol1:
            set_val = st.number_input(
                "Set mastery to", min_value=0, step=1, key="bulk_set_val", label_visibility="visible"
            )
            if st.button("Apply", key="btn_set_val"):
                n = bulk_set_mastery(selected_user, selected_tuples, int(set_val))
                st.session_state.pop("mastery_editor", None)
                _load_words.clear()
                st.toast(f"Set mastery to {int(set_val)} for {n} word(s)")

        with bcol2:
            st.write("")
            st.write("")
            if st.button("Reset to 0", key="btn_reset"):
                n = bulk_set_mastery(selected_user, selected_tuples, 0)
                st.session_state.pop("mastery_editor", None)
                _load_words.clear()
                st.toast(f"Reset {n} word(s) to 0")

        with bcol3:
            st.write("")
            st.write("")
            if st.button("+1", key="btn_plus"):
                n = bulk_delta_mastery(selected_user, selected_tuples, 1)
                st.session_state.pop("mastery_editor", None)
                _load_words.clear()
                st.toast(f"+1 mastery on {n} word(s)")

        with bcol4:
            st.write("")
            st.write("")
            if st.button("-1", key="btn_minus"):
                n = bulk_delta_mastery(selected_user, selected_tuples, -1)
                st.session_state.pop("mastery_editor", None)
                _load_words.clear()
                st.toast(f"-1 mastery on {n} word(s)")

        with bcol5:
            st.write("")
            st.write("")
            if st.button("Delete", type="secondary", key="btn_delete"):
                st.session_state["confirm_bulk_delete"] = True

        if st.session_state.get("confirm_bulk_delete"):
            st.warning(f"Delete {n_selected} word(s) from this user's progress? The corpus entries are kept.")
            yes_col, no_col, _ = st.columns([1, 1, 4])
            with yes_col:
                if st.button("Yes, delete", type="primary", key="confirm_yes"):
                    n = bulk_delete_words(selected_user, selected_tuples)
                    st.session_state.pop("confirm_bulk_delete", None)
                    st.session_state.pop("mastery_editor", None)
                    _load_words.clear()
                    st.toast(f"Deleted {n} word(s) from user progress")
            with no_col:
                if st.button("Cancel", key="confirm_no"):
                    st.session_state.pop("confirm_bulk_delete", None)


tab1, tab2 = st.tabs(["📊 Mastery Manager", "✏️ Word Editor"])

# ── Tab 1: Mastery Manager ─────────────────────────────────────────────────────
with tab1:
    st.header("Mastery Manager")
    _mastery_tab(selected_user, selected_topic)

# ── Tab 2: Word Editor ─────────────────────────────────────────────────────────
with tab2:
    st.header("Word Editor")
    st.caption("Edits apply to the shared corpus — all users will see the updated word and hint.")

    topic_arg2 = None if selected_topic == "All topics" else selected_topic
    editor_df = _load_words(selected_user, topic_arg2)

    if editor_df.empty:
        st.info("No words available for this user / topic.")
    else:
        ed_topics = sorted(editor_df["topic"].unique().tolist())
        ed_topic = st.selectbox("Topic", options=ed_topics, key="word_ed_topic")

        topic_words = editor_df[editor_df["topic"] == ed_topic]
        ed_word_id = st.selectbox("Word ID", options=topic_words["word_id"].tolist(), key="word_ed_word_id")

        current = topic_words[topic_words["word_id"] == ed_word_id].iloc[0]

        with st.form("word_edit_form"):
            new_word = st.text_input("Word text", value=current["word"])
            new_hint = st.text_input("Hint", value=current["hint"] if pd.notna(current["hint"]) else "")
            save_btn = st.form_submit_button("Save")

        if save_btn:
            if not new_word.strip():
                st.warning("Word text cannot be empty.")
            else:
                rowcount = update_word_corpus(ed_topic, ed_word_id, new_word.strip(), new_hint.strip())
                if rowcount > 0:
                    _load_words.clear()
                    st.toast(f"'{ed_word_id}' updated in corpus")
                    st.rerun()
                else:
                    st.warning("Update did not match any row — the word may have been deleted.")

