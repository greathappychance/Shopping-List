"""쇼핑 리스트 (Streamlit 버전)

웹 버전(index.html, app.js)과 같은 기능을 Streamlit으로 옮긴 것이다.
브라우저 localStorage 대신 서버의 JSON 파일에 저장하므로 새로고침하거나 서버를 다시 켜도 유지된다.

실행: streamlit run streamlit_app.py
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# 테스트는 SHOPPING_LIST_DATA로 다른 파일을 지정해 실제 데이터를 건드리지 않는다
DATA_FILE = Path(os.environ.get("SHOPPING_LIST_DATA") or Path(__file__).with_name("shopping_list_data.json"))
MAX_NAME_LENGTH = 100
UNDO_TIMEOUT_SEC = 5


# ---------- 저장소 ----------

def empty_data():
    return {"items": [], "edit_id": None, "last_deleted": None}


def is_valid_item(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("checked"), bool)
    )


def load_data():
    """파일이 없거나 깨졌으면 빈 리스트로 시작하고, 형식이 틀린 아이템은 걸러낸다."""
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_data()
    if not isinstance(raw, dict):
        return empty_data()

    data = empty_data()
    items = raw.get("items")
    data["items"] = [i for i in items if is_valid_item(i)] if isinstance(items, list) else []

    ids = {i["id"] for i in data["items"]}
    if raw.get("edit_id") in ids:
        data["edit_id"] = raw["edit_id"]

    last = raw.get("last_deleted")
    if isinstance(last, dict) and is_valid_item(last.get("item")):
        data["last_deleted"] = last
    return data


def save_data(data):
    # 임시 파일에 쓴 뒤 바꿔치기해서, 저장 도중 꺼져도 파일이 깨지지 않게 한다
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def find_item(data, item_id):
    return next((i for i in data["items"] if i["id"] == item_id), None)


def normalize_name(name):
    return str(name).strip()[:MAX_NAME_LENGTH]


# ---------- 동작 (버튼 콜백) ----------

def add_item():
    name = normalize_name(st.session_state.get("add_input", ""))
    if not name:
        return
    data = load_data()
    data["items"].append({
        "id": str(uuid.uuid4()),
        "name": name,
        "checked": False,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    })
    save_data(data)


def toggle_item(item_id):
    data = load_data()
    item = find_item(data, item_id)
    if item:
        item["checked"] = bool(st.session_state.get(f"check_{item_id}"))
        save_data(data)


def start_edit(item_id):
    # 한 번에 하나만 수정한다. 다른 아이템을 수정 중이었다면 그 수정은 취소된다
    data = load_data()
    if find_item(data, item_id):
        data["edit_id"] = item_id
        save_data(data)
        st.session_state.pop(f"edit_input_{item_id}", None)


def save_edit(item_id):
    data = load_data()
    item = find_item(data, item_id)
    name = normalize_name(st.session_state.get(f"edit_input_{item_id}", ""))
    if item and name:  # 빈 값이면 원래 이름 유지
        item["name"] = name
    data["edit_id"] = None
    save_data(data)


def cancel_edit():
    data = load_data()
    data["edit_id"] = None
    save_data(data)


def delete_item(item_id):
    data = load_data()
    index = next((n for n, i in enumerate(data["items"]) if i["id"] == item_id), None)
    if index is None:
        return
    removed = data["items"].pop(index)
    if data["edit_id"] == item_id:
        data["edit_id"] = None
    data["last_deleted"] = {"item": removed, "index": index, "deleted_at": time.time()}
    save_data(data)


def undo_delete():
    data = load_data()
    last = data["last_deleted"]
    if not undo_is_active(last):
        return
    index = min(int(last.get("index", 0)), len(data["items"]))
    data["items"].insert(index, last["item"])
    data["last_deleted"] = None
    save_data(data)
    # 복원된 아이템의 체크박스가 이전 상태로 남지 않게 위젯 상태를 지운다
    st.session_state.pop(f"check_{last['item']['id']}", None)


def clear_checked():
    data = load_data()
    if find_item(data, data["edit_id"]) and find_item(data, data["edit_id"])["checked"]:
        data["edit_id"] = None
    data["items"] = [i for i in data["items"] if not i["checked"]]
    save_data(data)


# ---------- 화면 ----------

MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~:$])")


def escape_markdown(text):
    """아이템 이름에 들어간 마크다운·HTML 기호가 서식으로 해석되지 않고 글자 그대로 보이게 한다."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)


@st.dialog("완료 항목 삭제")
def confirm_clear(count):
    st.write(f"완료 항목 {count}개를 삭제할까요?")
    left, right = st.columns(2)
    if left.button("삭제", key="confirm_clear", type="primary", width="stretch"):
        clear_checked()
        st.rerun()
    if right.button("취소", key="cancel_clear", width="stretch"):
        st.rerun()


def undo_is_active(last):
    return bool(last) and time.time() - last.get("deleted_at", 0) <= UNDO_TIMEOUT_SEC


def undo_banner(last):
    if not undo_is_active(last):
        return

    # 5초 뒤 숨기기는 브라우저의 CSS 애니메이션으로 한다.
    # st.fragment(run_every=1)로 서버에서 주기적으로 다시 그리면 그 사이의 버튼 클릭이 사라지기 때문이다.
    # 같은 삭제 건이면 다시 그려도 지연 시간이 바뀌지 않도록, 처음 그릴 때의 남은 시간을 세션에 기억한다
    key = f"undo_{int(last['deleted_at'] * 1000)}"
    delays = st.session_state.setdefault("undo_delays", {})
    delay = delays.setdefault(key, max(0.0, UNDO_TIMEOUT_SEC - (time.time() - last["deleted_at"])))
    st.markdown(
        f"<style>.st-key-{key}, .st-key-{key} * {{ animation: undo-hide 0s linear {delay:.2f}s forwards; }}"
        "@keyframes undo-hide { to { display: none; visibility: hidden; opacity: 0; pointer-events: none; } }</style>",
        unsafe_allow_html=True,
    )

    with st.container(border=True, key=key):
        left, right = st.columns([3, 1], vertical_alignment="center")
        left.markdown(f"'{escape_markdown(last['item']['name'])}' 삭제됨")
        right.button("실행 취소", key="undo", on_click=undo_delete, width="stretch")


def render_item(item, editing):
    item_id = item["id"]

    if editing:
        with st.form(f"edit_form_{item_id}", border=False):
            col_input, col_save, col_cancel = st.columns([6, 1.3, 1.3], vertical_alignment="center")
            col_input.text_input(
                "이름 수정",
                value=item["name"],
                max_chars=MAX_NAME_LENGTH,
                key=f"edit_input_{item_id}",
                label_visibility="collapsed",
            )
            # Enter를 누르면 첫 번째 제출 버튼(저장)이 눌린 것으로 처리된다
            col_save.form_submit_button("저장", type="primary", on_click=save_edit, args=(item_id,), width="stretch")
            col_cancel.form_submit_button("취소", on_click=cancel_edit, width="stretch")
        return

    col_check, col_edit, col_delete = st.columns([6, 1.3, 1.3], vertical_alignment="center")
    label = escape_markdown(item["name"])
    if item["checked"]:
        label = f":gray[~~{label}~~]"
    col_check.checkbox(
        label,
        value=item["checked"],
        key=f"check_{item_id}",
        on_change=toggle_item,
        args=(item_id,),
    )
    col_edit.button("수정", key=f"edit_{item_id}", on_click=start_edit, args=(item_id,), width="stretch")
    col_delete.button("삭제", key=f"delete_{item_id}", on_click=delete_item, args=(item_id,), width="stretch")


def main():
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    st.markdown(
        """
        <style>
          .block-container { max-width: 560px; padding-top: 2.5rem; }
          div[data-testid="stCheckbox"] p { overflow-wrap: anywhere; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    data = load_data()
    items = data["items"]
    checked_count = sum(1 for i in items if i["checked"])

    title, count = st.columns([3, 2], vertical_alignment="center")
    title.markdown("<h2 style='white-space:nowrap;padding:0'>🛒 쇼핑 리스트</h2>", unsafe_allow_html=True)
    count.markdown(f"<p style='text-align:right;color:gray;margin:0'>전체 {len(items)}개 / 완료 {checked_count}개</p>", unsafe_allow_html=True)

    # Enter 또는 추가 버튼으로 제출하고, 제출 후 입력창을 비운다
    with st.form("add_form", clear_on_submit=True, border=False):
        col_input, col_button = st.columns([5, 1.2], vertical_alignment="bottom")
        col_input.text_input(
            "추가할 물건",
            max_chars=MAX_NAME_LENGTH,
            placeholder="살 물건을 입력하세요",
            key="add_input",
            label_visibility="collapsed",
        )
        col_button.form_submit_button("추가", type="primary", on_click=add_item, width="stretch")

    st.divider()

    if not items:
        st.markdown("<p style='text-align:center;color:gray;padding:2rem 0'>📝<br>장 볼 물건을 추가해 보세요</p>", unsafe_allow_html=True)
    for item in items:
        render_item(item, editing=item["id"] == data["edit_id"])

    st.divider()

    _, col_clear = st.columns([3, 1.6])
    if col_clear.button("완료 항목 삭제", disabled=checked_count == 0, width="stretch"):
        confirm_clear(checked_count)

    undo_banner(data["last_deleted"])


main()
