"""Claude との会話（「置くところを少し手前にして」）の、プログラムが受け持つ部分。

唐川さんの決め事（2026-11-03 夜）と docs/LOCATE_DESIGN.md による。役割の分け方：
- Claude（またはモック）：意図の理解・位置の候補（行番号の根拠つき）・聞き返し。
- プログラム（ここ）：候補の照合、根拠の文、方向の選択肢、量の読み取り、会話の状態、決まった文の組み立て。
  座標の変更・ガード（20mm・R1・B1/B6・D1/D2・check_proposal）・承認（ボタンだけ）は今の流れのまま。
  組み立てた指示は「C00005をYのマイナス3ミリ」のような決まった文にして、今の流れ（Session.hear → 解釈の係 →
  Session.propose → check_proposal → plan_shift）にそのまま通す。ここではジョブを変えない。

守ること：
- 位置の候補は「選ぶ」だけ。1件でも自動では選ばない（人がボタンで選ぶか、短い肯定で選ぶ。選択であって承認ではない）。
- 根拠の文はプログラムが命令の行から作る。Claude の文は使わない（行番号だけ）。DOUT・TIMER の意味は断定しない
  （「信号を切る」「待つ」と、命令の事実だけを書く）。
- 推定した名前（「置くところ＝C00005」）は、その画面の中だけで「推定（未確定）」として扱う。保存しない（仕様 v2.1 の19）。
- 手前・奥・前・後ろ・右・左は軸に当てはめない（FRAME の向きの表が無い）。方向は X・Y・Z の＋−の6つの選択肢から人が選ぶ。
- 量は人の言葉だけから読む（算用数字＋mm／ミリ）。量の選択肢は出さない。
- 案B（FRAME の label・aliases）のつなぎ口は frame_lookup。FRAME はまだ無いので、今は使われない。
- 上下の向き（CHECKER_026 M2）：方向のボタンの「（上）」「（下）」と、会話の「上に・下げて」を Z にすることは、
  上下の向きの照合に通ったとき（updown.UpdownStatus.confirmed）だけ。照合の結果は呼ぶ側（web/flow.py）が毎回渡す。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from . import jbi
from . import updown as ud
from . import utterance as ut

# 場所の言葉（feat/nl-polish の utterance.PLACE_WORDS と同じ一覧。facts.place_ref が無いときだけ、ここで探す）
PLACE_WORDS = tuple(getattr(ut, "PLACE_WORDS", ("置くところ", "置き場所", "置く位置", "取るところ", "つかむところ", "離すところ")))
_PICK_WORDS = ("取るところ", "つかむところ")  # それ以外（置く・離す）は「置く側」
_PLACE_RE = re.compile("|".join(re.escape(w) for w in sorted(PLACE_WORDS, key=len, reverse=True)))
# 向きの言葉（utterance._RELATIVE_DIR と同じ形）。読み取りのときは「側」「の方」「方向」「に」「へ」まで一緒に外す
_RELATIVE_RE = re.compile(r"手前|後ろ|奥|(?<!変更)(?<!以)(?<!名)前|右|左")
_RELATIVE_PHRASE = re.compile(r"(?:手前|後ろ|奥|(?<!変更)(?<!以)(?<!名)前|右|左)(?:側|の方|方向)?(?:に|へ)?")

# 位置の確認の段だけで使う短い肯定（唐川さんの例「そうそう」）。選択であって承認ではない
AFFIRMATIVES = ("はい", "そう", "そうそう", "それ", "うん", "お願いします")
_AFFIRM_STRIP = re.compile(r"[\s、。,.!！]+")

MAX_CANDIDATES = 3
MAX_EVIDENCE = 5
DIRECTIONS = ("X+", "X-", "Y+", "Y-", "Z+", "Z-")
ESTIMATE_LABEL = "推定（未確定）"
MOCK_SPEAKER = "モック（テスト用）"

# ---- 画面に出す決まった文（数字・位置番号・言葉はプログラムが入れる） ----
ASK_ONE = "「{word}」は {pos} のことでしょうか？"
ASK_MANY = "「{word}」の候補が {n}つあります。どれですか？"
PICK_PROMPT = "位置を選んでください（図の点か番号を押します）"
LOCATE_FAILED = "「{word}」がどの位置か、ジョブから絞り込めませんでした。" + PICK_PROMPT
NOT_SYNTHETIC = ("このジョブは架空のサンプルではないため、ジョブの中身を Claude に送りません。"
                 "「{word}」に当たる" + PICK_PROMPT)
NO_LOCATOR = "今の解釈の係は、位置の候補を出せません。" + PICK_PROMPT
LOCATE_ERROR = "{error}。" + PICK_PROMPT
ASK_MOVE = "どちらの方向（X・Y・Z の＋−）に、何mm動かしますか？"
ASK_DIR = "{amount}mm ですね。どちらの方向（X・Y・Z の＋−）に動かしますか？"
ASK_SIGN = "{amount}{axis} のプラスですか、マイナスですか？"
ASK_AXIS = "{amount}{sign}ですね。どの軸（X・Y・Z）ですか？"
ASK_AMOUNT = "{dir} ですね。何mm動かしますか？（例：3mm）"
RELATIVE_NOTE = "「{rel}」が X・Y・Z のどれに当たるかは、まだ決まっていません（推測しません）。"
RELATIVE_ASK = "「{rel}」が X・Y・Z のどれに当たるかは、まだ決まっていません（推測しません）。ボタンで方向を選んでください"
CONFIRM_STAY = "位置はボタンで選んでください（候補が1つのときは「はい」と言っても選べます）"
AFFIRM_MANY = "候補が2つ以上あります。どの位置か、ボタンで選んでください"
SIGN_CONFLICT = "向きが前の答えと違います。方向をボタンで選び直してください"
AXIS_CONFLICT = "軸が前の答えと違います。方向をボタンで選び直してください"
NOTHING_READ = "方向か量が読めませんでした。"
DONE = "変更前後を表示します。これでよろしいですか？（承認は確認表示のボタンで行います）"
END_UNDO = "会話をやめました。もう一度、指示してください"
STALE = "この選択肢は古くなりました。もう一度話してください"
PENDING = "承認待ちの変更候補があります。比べるか、元に戻してから指示してください"
OTHER_LABEL = "ほかの位置"
# 位置の確認の段（「ほかの位置」のボタンが見えている）での打ち消し（「やっぱり違う」）。止まる動きは変えない（CHECKER_026 S-pick）
PICK_AFTER_NEGATION = "分かりました。合っていなければ「" + OTHER_LABEL + "」を押してください"
TWO_PLACES = "位置が2つあります。1つずつ指示してください"
GO_BUTTON = "動かすときは、上のボタンを押してください（この段では、声や文字では進めません）"
ENDED_NEW = "会話をやめて、新しい指示として読みました。"
ENDED_RESELECT = "位置を選び直したため、前の指示をリセットしました。新しい位置について教えてください。"
AFFIRM_BUTTON = "位置はボタンで選んでください（「はい」などは、位置の候補を聞いたすぐあとだけ使えます）"
ZERO_AMOUNT = "移動量が0のため、変更しません"
CONFIRM_GO = "{pos} を {dir} {amount}mm 動かしますか？"
CONFIRM_GO_LABEL = "{pos} を {dir} {amount}mm 動かす"
# ふつうの流れ（utterance._pre_check_core）と同じ文
STRAY_SIGN = "プラス・マイナスがどこに付くか分かりません。「Xのマイナス5ミリ」のように、軸のすぐ後に言ってください"
TWO_SIGNS = "向きの指定が2つあります。どちらの向きか、もう一度指示してください"
SIGN_NEEDS_AXIS = "プラス・マイナスは、軸と一緒に言うか（例：Yのマイナス3ミリ）、ボタンで方向を選んでください"
# 会話が受け持つ聞き返し（これ以外の pre_check の止める・聞き返す理由は、会話にせずそのまま返す）
_HANDLED_ASKS = ("何mm動かしますか？", ut.AXIS_ASK, ut.AXIS_ASK_UPDOWN)


# ---- 言葉を見つける ----
def find_place_word(text: str, facts: object = None) -> str | None:
    """場所の言葉（「置くところ」など）。facts.place_ref（feat/nl-polish）があればそれを使う。"""
    if facts is not None and hasattr(facts, "place_ref"):
        w = getattr(facts, "place_ref")
        return w if isinstance(w, str) and w in PLACE_WORDS else None
    m = _PLACE_RE.search(unicodedata.normalize("NFKC", text))
    return m.group(0) if m else None


def find_relative_dir(text: str, facts: object = None) -> str | None:
    """向きの言葉（手前・奥・前・後ろ・右・左）。軸には当てはめない。表示と聞き返しのためだけ。"""
    if facts is not None and hasattr(facts, "relative_dir"):
        found = getattr(facts, "relative_dir")
        if isinstance(found, str):
            return found or None
        return found[0] if found else None
    m = _RELATIVE_RE.search(unicodedata.normalize("NFKC", text))
    return m.group(0) if m else None


def count_place_words(text: str) -> int:
    """場所の言葉の数（「置くところと取るところ」は2）。"""
    return len(_PLACE_RE.findall(unicodedata.normalize("NFKC", text)))


def has_neighbour_relative(text: str, word: str) -> bool:
    """「置くところの手前」のように、場所の言葉＋「の」＋向きの言葉（となりの位置のことかもしれない）。"""
    t = unicodedata.normalize("NFKC", text)
    return re.search(re.escape(word) + r"\s?の\s?(?:" + _RELATIVE_RE.pattern + ")", t) is not None


def has_sign_word(text: str) -> bool:
    return _SIGN_TOKEN.search(unicodedata.normalize("NFKC", text).upper()) is not None


def handled_by_dialogue(facts: ut.UtteranceFacts, updown: "ud.UpdownStatus | None" = None) -> bool:
    """場所の言葉を置き換えた文の pre_check が、通るか、会話が受け持つ理由（量・方向が無い）で聞き返すときだけ真。
    上下の向きが確認できていない「上に・下げて」は、pre_check が状態ごとの案内で聞き返す（会話は受け持たない）。"""
    v = ut.pre_check(facts, None, updown)
    if v.status == "ok":
        return True
    if v.status != "ask":
        return False
    if v.message in _HANDLED_ASKS:
        return True
    # 「少し」など、はっきりした量が1つも無いあいまいな量（あとで何mmかを聞く）
    if facts.unclear_amounts and not facts.amounts and v.message.startswith("量がはっきりしません"):
        return True
    # 軸だけで向きが無い（「Y方向に3mm」）：その軸の＋−のボタンで聞く
    a = facts.axis
    return a is not None and v.message.startswith(f"{a}方向のプラスですか、マイナスですか")


def is_affirmative(text: str) -> bool:
    """位置の確認への短い肯定か（一覧の言葉ちょうど1つだけ。「OK」「承認」「それで」は入らない）。"""
    return _AFFIRM_STRIP.sub("", unicodedata.normalize("NFKC", text)) in AFFIRMATIVES


def substitute_opening(text: str, word: str) -> str:
    """始めの発話から、場所の言葉を「この位置」に、向きの言葉を「移動」に置き換えた文（量・軸・符号を読むためだけ）。

    「置くところを少し手前にして」→「この位置を少し移動して」。向きの言葉は軸にしない（ここで消す）。
    """
    t = unicodedata.normalize("NFKC", text).replace(word, "この位置")
    return _RELATIVE_PHRASE.sub("移動", t)


# ---- ジョブの命令の行と、根拠の文 ----
def instruction_lines(job: jbi.Job) -> list[tuple[int, str]]:
    """命令の行（//INST の部分）と、ファイルの行番号（1から）。//POS の行・座標値は入れない。"""
    return [(ins.index + 1, ins.text) for ins in job.instructions]


_MOVE_LINE = re.compile(r"^(MOVL|MOVJ|MOVC|MOVS)\s+(C[0-9]{5})\b")
_DOUT = re.compile(r"^DOUT\s+OT#\([0-9]+\)\s+(ON|OFF)\s*$")


def evidence_note(text: str) -> str:
    """命令の行の事実だけを短く書く（意味は断定しない）。分からない命令は空。"""
    m = _MOVE_LINE.match(text)
    if m:
        return f"{m.group(2)} へ直線で動く" if m.group(1) == "MOVL" else f"{m.group(2)} へ動く"
    d = _DOUT.match(text)
    if d:
        return "信号を入れる" if d.group(1) == "ON" else "信号を切る"
    if re.match(r"^TIMER\b", text):
        return "待つ"
    if text.startswith("'"):
        return "コメント"
    return ""


@dataclass(frozen=True)
class Evidence:
    line_no: int
    text: str
    note: str

    def as_dict(self) -> dict:
        return {"line_no": self.line_no, "text": self.text, "note": self.note}


@dataclass(frozen=True)
class Candidate:
    position: str
    rank: int
    evidence: tuple[Evidence, ...]
    confirmed_by_frame: bool = False

    def as_dict(self) -> dict:
        return {"position": self.position, "rank": self.rank, "evidence": [e.as_dict() for e in self.evidence],
                "confirmed_by_frame": self.confirmed_by_frame}


def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def verify_candidates(job: jbi.Job, raw: object) -> list[Candidate]:
    """解釈の係（Claude／モック）の答えを、プログラムが照合する。合わないものは捨てる（推測で補わない）。

    - 形：1〜3件の並び。各件はちょうど position・evidence_lines・rank の3つ（余計な項目・自由文があれば捨てる）
    - position はジョブの位置。rank は 1〜3 で重ならない。同じ位置が2回あれば後のものを捨てる
    - evidence_lines は 1〜5件の、重ならない命令の行番号（すべて //INST の範囲）。
      少なくとも1行が、その位置を参照する命令か、そのすぐ前・すぐ後の命令
    """
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CANDIDATES:
        return []
    order = [ins.index + 1 for ins in job.instructions]
    by_line = {ins.index + 1: ins for ins in job.instructions}
    out: list[Candidate] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"position", "evidence_lines", "rank"}:
            continue
        pos, lines, rank = item["position"], item["evidence_lines"], item["rank"]
        if not isinstance(pos, str) or pos not in job.positions:
            continue
        if not _is_int(rank) or not 1 <= rank <= MAX_CANDIDATES:
            continue
        if (not isinstance(lines, list) or not 1 <= len(lines) <= MAX_EVIDENCE
                or not all(_is_int(n) and n in by_line for n in lines) or len(set(lines)) != len(lines)):
            continue

        def refers(n: int) -> bool:
            i = order.index(n)
            near = [order[j] for j in (i - 1, i, i + 1) if 0 <= j < len(order)]
            return any(pos in by_line[k].refs for k in near)

        if not any(refers(n) for n in lines):
            continue
        if any(c.position == pos or c.rank == rank for c in out):
            continue
        out.append(Candidate(pos, rank, tuple(Evidence(n, by_line[n].text, evidence_note(by_line[n].text))
                                              for n in sorted(lines))))
    return sorted(out, key=lambda c: c.rank)


def frame_lookup(frame: object, word: str) -> str | None:
    """案B のつなぎ口：FRAME の label・aliases（専門家が書いて review したもの）に一致する位置。1つに決まらなければ None。"""
    if not isinstance(frame, Mapping):
        return None
    positions = frame.get("positions")
    if not isinstance(positions, Mapping):
        return None
    hits = []
    for name, info in positions.items():
        if not isinstance(info, Mapping):
            continue
        names = [info.get("label")] + list(info.get("aliases") or [])
        if word in [n for n in names if isinstance(n, str)]:
            hits.append(name)
    return hits[0] if len(hits) == 1 and isinstance(hits[0], str) else None


def frame_candidate(job: jbi.Job, position: str) -> Candidate | None:
    """FRAME で決まった位置の候補（根拠は、その位置を参照する命令の行。最大5行）。"""
    if position not in job.positions:
        return None
    ev = tuple(Evidence(i.index + 1, i.text, evidence_note(i.text)) for i in job.users_of(position))[:MAX_EVIDENCE]
    return Candidate(position, 1, ev, confirmed_by_frame=True)


# ---- 方向と量 ----
def direction_label(d: str, names: Mapping | None = None) -> str:
    """X+ → 「X＋」、Z+ → 「Z＋（上）」。names：確認済みの方向の名前（updown.UpdownStatus.direction_names）。
    渡さなければ名前を添えない（上下の向きが確認できていないときは「（上）」「（下）」を付けない）。"""
    axis, sign = d[0], d[1]
    names = names if isinstance(names, Mapping) else {}
    group = names.get(axis)
    name = group.get(sign) if isinstance(group, Mapping) else None
    return f"{axis}{'＋' if sign == '+' else '−'}" + (f"（{name}）" if name else "")


def fmt_amount(v: float) -> str:
    """量の書き方（指数の形にしない。3.0 → 3、2.50 → 2.5）。"""
    text = format(Decimal(str(v)).normalize(), "f")
    return text


def canonical_text(position: str, axis: str, sign: int, amount: float, vertical: bool = False) -> str:
    """会話で集めたことから、今の流れに通す決まった指示の文を作る（例「C00005をYのマイナス3ミリ」）。

    vertical：方向を「上に・下げて」から読んだ（Z）。そのときは「C00005を3ミリ下げて」のまま通し、上下の向きの照合を
    ふつうの指示と同じくもう一度通す（会話の途中で設定が変わっても、Z の文字に書き換えて通さない。CHECKER_026 再修正 P0-1）。
    """
    if vertical and axis == "Z":
        return f"{position}を{fmt_amount(amount)}ミリ{'上げて' if sign > 0 else '下げて'}"
    return f"{position}を{axis}の{'プラス' if sign > 0 else 'マイナス'}{fmt_amount(amount)}ミリ"


_SIGN_TOKEN = re.compile(r"マイナス|プラス|[+\-]")
_SIGN = {"+": 1, "プラス": 1, "-": -1, "マイナス": -1}


@dataclass(frozen=True)
class Slots:
    amount: float | None = None
    axis: str | None = None
    sign: int | None = None
    problem: str | None = None
    stop: bool = False  # problem を「止めた」として返す（0mm）
    vertical: bool = False  # 軸（Z）を「上に・下げて」から読んだ（上下の向きが確認済みのときだけ）


def read_slots(facts: ut.UtteranceFacts, opening: bool = False, updown: "ud.UpdownStatus | None" = None) -> Slots:
    """発話から、量（mm）・軸・向きを、はっきり言ったものだけ取り出す。あいまいなら problem（聞き返す文）。

    opening（始めの発話）のときは、あいまいな量（「少し」）は量なしとして扱う（あとで聞く）。
    上下の向きが確認できていない「上に・下げて」（facts.vertical）は、Z にせず状態ごとの案内を返す。
    """
    st = updown if isinstance(updown, ud.UpdownStatus) else ud.NOT_CONFIGURED
    if facts.vertical and not st.confirmed:
        return Slots(problem=st.message)
    a_list = facts.amounts
    if facts.unclear_amounts:
        if not opening:
            return Slots(problem=f"量がはっきりしません（{'・'.join(facts.unclear_amounts)}）。算用数字と mm（ミリ）で言ってください")
        a_list = ()
    if len(a_list) > 1:
        return Slots(problem="量が2つ以上あります。1つの量で言ってください")
    if a_list and a_list[0].unit != "mm":
        return Slots(problem="移動の量は mm（ミリ）で言ってください")
    amount = a_list[0] if a_list else None
    if amount is not None and amount.value == 0:
        return Slots(problem=ZERO_AMOUNT, stop=True)  # 解釈の係を呼ぶ前に断る
    signs = ([amount.sign] if amount is not None and amount.sign is not None else []) + list(facts.axis_signs)
    if facts.stray_signs > 0:
        return Slots(problem=STRAY_SIGN)  # はぐれた符号・重なった符号はふつうの流れと同じく聞き返す（ボタンを絞らない）
    if len(signs) > 1:
        return Slots(problem=TWO_SIGNS)  # 同じ向きの重なり（「Y-方向にマイナス3mm」）も聞き返す
    if len(facts.axes) > 1:
        return Slots(problem=f"方向が2つ以上あります（{'・'.join(facts.axes)}）。推測しません。ボタンで1つ選んでください")
    if signs and not facts.axes:
        return Slots(problem=SIGN_NEEDS_AXIS)  # 軸の無い符号（「マイナス3mm」）でボタンを絞らない
    axis = facts.axes[0] if facts.axes else None
    return Slots(amount=amount.value if amount is not None else None,
                 axis=axis, sign=signs[0] if signs else None,
                 vertical=bool(facts.vertical and axis == "Z" and "Z" not in facts.explicit_axes))


# ---- 会話の状態（画面ごと・メモリの中だけ） ----
@dataclass
class Dialog:
    step: str | None = None          # None / "confirm_position" / "pick" / "ask_move"
    turn: int = 0                    # 選択肢の番号（古い選択肢を使わないため）。選択肢が変わるたびに増やす
    word: str | None = None
    relative: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    position: str | None = None
    amount: float | None = None
    axis: str | None = None
    sign: int | None = None
    estimated: dict | None = None    # {"position", "word", "label", "by"}。保存しない
    turns: list[dict] = field(default_factory=list)
    proposal_mark: int = -1          # 位置の候補を聞いた直後の turns の数（声の肯定は、このときだけ使える）
    confirm_go: bool = False         # 手で選んだ位置で、方向と量がそろった：ボタン1つで確かめる
    # 確認済みの方向の名前（上下の向きの照合に通ったときだけ Z の 上・下）。web/flow.py が要求のたびに入れ直す
    direction_names: dict = field(default_factory=dict)
    # 方向（Z）を「上に・下げて」から読んだ。最後の決まった文も「上げて・下げて」で作り、上下の向きの照合をもう一度通す
    vertical: bool = False

    MAX_TURNS = 40

    def start(self, word: str, relative: str | None, slots: Slots) -> None:
        self.step, self.word, self.relative = None, word, relative
        self.candidates, self.position = [], None
        self.amount, self.axis, self.sign = slots.amount, slots.axis, slots.sign
        self.vertical = slots.vertical
        self.turns = []
        self.proposal_mark, self.confirm_go = -1, False

    def end(self, clear_estimate: bool = True) -> None:
        """会話を終える。推定（未確定）の名前も消す（候補ができたとき・断っただけのときは、呼んだ側が残す）。"""
        self.step = None
        self.candidates = []
        self.confirm_go = False
        if clear_estimate:
            self.estimated = None
        self.turn += 1

    def say(self, who: str, text: str, kind: str = "say") -> None:
        self.turns.append({"who": who, "text": text, "kind": kind})
        del self.turns[: -self.MAX_TURNS]

    @property
    def direction(self) -> str | None:
        if self.axis is None or self.sign is None:
            return None
        return f"{self.axis}{'+' if self.sign > 0 else '-'}"

    def complete(self) -> bool:
        return self.position is not None and self.amount is not None and self.direction is not None

    def direction_choices(self) -> list[str]:
        if self.direction is not None:
            return []
        out = list(DIRECTIONS)
        if self.axis is not None:
            out = [d for d in out if d[0] == self.axis]
        if self.sign is not None:
            out = [d for d in out if d[1] == ("+" if self.sign > 0 else "-")]
        return out

    def choices(self) -> list[dict]:
        if self.step == "confirm_position":
            return ([{"id": f"pos:{c.position}", "label": f"{c.position} です"} for c in self.candidates]
                    + [{"id": "other", "label": OTHER_LABEL}])
        if self.step == "ask_move" and self.confirm_go and self.complete():
            return [{"id": "go", "label": CONFIRM_GO_LABEL.format(
                pos=self.position, dir=f"{self.axis} {'＋' if self.sign > 0 else '−'}".split()[0],
                amount=("＋" if self.sign > 0 else "−") + fmt_amount(self.amount))}]
        if self.step == "ask_move":
            amt = f"{fmt_amount(self.amount)}mm" if self.amount is not None else ""
            out = []
            for d in self.direction_choices():
                label = direction_label(d, self.direction_names)
                if amt:
                    label += amt if label.endswith("）") else f" {amt}"
                out.append({"id": f"dir:{d}", "label": label})
            return out
        return []

    def confirm_go_message(self) -> str:
        return CONFIRM_GO.format(pos=self.position, dir=self.axis,
                                 amount=("＋" if self.sign > 0 else "−") + fmt_amount(self.amount))

    def ask_move_message(self) -> str:
        amt = f"{fmt_amount(self.amount)}mm" if self.amount is not None else None
        if self.direction is not None:
            return ASK_AMOUNT.format(dir=direction_label(self.direction, self.direction_names))
        if amt is None and self.axis is None and self.sign is None:
            msg = ASK_MOVE
        elif self.axis is not None:
            msg = ASK_SIGN.format(amount=f"{amt} ですね。" if amt else "", axis=self.axis)
        elif self.sign is not None:
            msg = ASK_AXIS.format(amount=amt or "", sign="（マイナス）" if self.sign < 0 else "（プラス）")
        else:
            msg = ASK_DIR.format(amount=fmt_amount(self.amount))
        if self.relative:
            msg += RELATIVE_NOTE.format(rel=self.relative)
        return msg
