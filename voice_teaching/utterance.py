"""発話（音声認識の文字・文字入力）の読み取りと、Claude の提案との照らし合わせ。Claude に頼らない、プログラムだけの部分。

方針（docs/VOICE_DESIGN.md、仕様v2.1 5-3・6章）:
- 発話からは、はっきり書かれた事実だけを取り出す。あいまいな言葉を推測で数字や軸にしない。
- 量として読むのは「算用数字（全角・半角）＋ mm／ミリ／%」だけ。漢数字・「半分」・単位の無い数字は、量が分からないものとして聞き返す。
- 「右」「左」は枠の設定の軸で決めるもの。枠がまだ無いので、軸を決めずに聞き返す。
- Claude（またはモック）の提案は、取り出した事実と一致したときだけ通す。食い違えば聞き返す。
- 承認・書き出し・転送・ロボットの起動は、声や文字では行わない。
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from . import updown as ud

# 画面にも文章にも出さない、安全や距離を断定する言葉（CLAUDE.md・受入テスト18）
FORBIDDEN_WORDS = (
    "安全です", "安全な", "安全に", "安全でした",
    "当たります", "当たりません", "当たらない位置", "当たらないので",
    "干渉します", "干渉しません", "干渉しない",
    "ぶつかります", "ぶつかりません", "ぶつからない",
    "問題ありません", "大丈夫です",
)

OPS = ("move", "speed", "compare", "undo")  # 1つの発話で1つだけ
KIND_ORDER = ("select", "move", "speed", "compare", "undo", "approve", "start")

APPROVE_MESSAGE = ("承認とシミュレーション用の書き出しは、画面のボタンで行います。声や文字の指示では行いません。"
                   "実機への転送・実機用の出力・ロボットの直接操作には対応していません")
START_MESSAGE = "このアプリからはロボットを動かしません。ロボットはペンダントで選んで、人がスタートします"
# 「右」「左」の案内。状態を知らずに出す固定の文（Claude の経路・提案の照らし合わせ）は、上下の言葉を例に出さない（CHECKER_026 M2）。
# 上下の向きが確認済みのときだけ、pre_check が SIDE_MESSAGE_UPDOWN（「上げて」「下げて」の例つき）を出す
SIDE_MESSAGE = ("「右」「左」は、専門家が枠の設定で軸を決めるまで使えません（軸が決まっていません）。"
                "Zプラス・Yのマイナスのように、軸（X・Y・Z）と向きで指示してください")
SIDE_MESSAGE_UPDOWN = ("「右」「左」は、専門家が枠の設定で軸を決めるまで使えません（軸が決まっていません）。"
                       "X・Y・Z の方向、または「上げて」「下げて」で指示してください")
# 方向を聞く文。確認済みのときだけ上げる・下げるを例に出す。dialog._HANDLED_ASKS も両方を見る
AXIS_ASK = "どの方向（Zプラス・Yのマイナスのように、軸と向き）に動かしますか？"
AXIS_ASK_UPDOWN = "どの方向（X・Y・Z、または上げる・下げる）に動かしますか？"
FRONT_BACK_MESSAGE = "前・後ろの向きは、このアプリでは扱いません"


def contains_forbidden(text: str) -> bool:
    return any(w in text for w in FORBIDDEN_WORDS)


# ---- 正規化 ----
_MINUS = str.maketrans({"−": "-", "‐": "-", "‑": "-", "–": "-"})


def normalize(text: str) -> str:
    """全角英数字を半角に（NFKC）、英字を大文字にする。"""
    # 音声認識が返すマイナス記号（−・‐ など）も「-」として読む
    return unicodedata.normalize("NFKC", text).upper().translate(_MINUS)


def normalize_position(text: str) -> str | None:
    """'c00005'・'Ｃ００００５' などを 'C00005' にする。形が違えば None。"""
    t = normalize(text).strip()
    m = re.fullmatch(r"C\s?([0-9]{5})", t)
    return f"C{m.group(1)}" if m else None


# ---- 取り出した事実 ----
@dataclass(frozen=True)
class Amount:
    value: float          # 符号を除いた量
    unit: str             # "mm" または "%"
    sign: int | None      # 数字の前の +／-／プラス／マイナス。無ければ None
    text: str             # 発話の中の書き方


@dataclass(frozen=True)
class UtteranceFacts:
    raw: str
    text: str
    kinds: tuple[str, ...] = ()
    positions: tuple[str, ...] = ()
    bad_positions: tuple[str, ...] = ()
    amounts: tuple[Amount, ...] = ()
    unclear_amounts: tuple[str, ...] = ()
    axes: tuple[str, ...] = ()
    axis_signs: tuple[int, ...] = ()       # 方向の言葉の向き（X-方向、上げて など）
    sides: tuple[str, ...] = ()            # 右・左（軸は枠で決める）
    speed_signs: tuple[int, ...] = ()      # 速度を上げる +1 ／下げる -1
    refers_to_selection: bool = False      # 「この位置」「ここ」など
    stray_signs: int = 0                   # 量にも軸にも付かなかった符号（＋・−・プラス・マイナス）の数
    vertical: bool = False                 # 「上げ・下げ・上に・下に」を Z（上下）として読んだ
    # 軸の文字（X・Y・Z）ではっきり言った軸（「Zマイナス」「Zの」「Z座標」「Z方向」など）。上・下から補った Z は入れない。
    # 上下の向きが確認済みでないときは、check_proposal が Z の提案をこの中に Z があるときだけ通す（最後の止め。CHECKER_026 M2）
    explicit_axes: tuple[str, ...] = ()
    # 軸の文字＋値の増減で言った軸（「Z座標を3mm下げて」「Zの値を減らして」＝その軸の値を減らす。唐川さんの判断 2026-11-03）。
    # 物理的な上下の指示（vertical：「3mm下げて」から補った Z）とは別の項目で持つ。上下の向きの照合は要らない（今どおり許可）。
    # 同じ発話に「上に・下に」もあれば vertical も立ち、上下の指示として扱う（未確認なら聞き返す）
    value_axes: tuple[str, ...] = ()
    stop_reasons: tuple[str, ...] = ()
    # 読み取りで使わなかった言葉（つなぎの言葉の一覧 FILLER_WORDS にも無いもの）。
    # 移動・速度の指示で1つでもあれば聞き返す（「バイオ 3ミリ 下げて」を Z −3 にしない。docs/UNKNOWN_WORDS_PLAN.md）
    unknown_words: tuple[str, ...] = ()
    # 打ち消し・取り消し・言い直しの言葉（「下げないで」「駄目」「じゃなくて」）。移動・速度の指示にあれば止める（CHECKER_023A B6・B7）
    negations: tuple[str, ...] = ()
    # 元に戻す言葉そのもの（「やめて」「取り消して」）の打ち消し。「やめて」だけの元に戻す指示（_plain_undo）のときは空。
    # 空でなければ、どの指示でも止める（「キャンセルはやめて」「C00004を選ぶのはやめて」）
    undo_negations: tuple[str, ...] = ()
    # 2026-11-03 夜の決定（docs/UNKNOWN_WORDS_PLAN.md 7章）。対話の機能・画面から使う
    question: bool = False                 # 疑問の形（文の終わりの ？・?・ですか など）。候補にせず、確かめる
    question_text: str | None = None       # ？・か を外した同じ指示（確かめる選択肢の文）。答えと合わせたときは None
    repeat: tuple[int | None, str] | None = None  # 繰り返し・往復 (回数, "ambiguous"／"times"／"round_trip")。候補にしない
    place_ref: str | None = None           # 「置くところ」などの場所の言葉（どの位置かはプログラムが決めない）
    place_as_destination: bool = False     # 場所の言葉を行き先に使った（「置くところに」「置き場所へ」「置く位置まで」）
    relative_dir: tuple[str, ...] = ()     # 手前・奥・前・後ろ・右・左（軸には当てない。止める・聞き返す）
    # 文の終わりの「動かします」「移動します」「ずらします」を外した指示の文（CHECKER_026 M3）。候補にせず、
    # 変更の希望か操作の説明かを確かめる。答えと合わせたとき・疑問の形のときは None
    shimasu_text: str | None = None
    # 単位の無い・違う数のすぐ前の符号（「Z+3」の +、「Yプラスの方向にマイナス3」の マイナス）。この発話の中のもの。
    # 量は作らないので向きには使わないが、捨てない（Safety High-1）。軸・量の符号と両方あれば、向きの指定が2つとして止める
    unclear_signs: tuple[int, ...] = ()
    # この発話に出た、向きの言葉の全部（+ − プラス マイナス、上げ・上に・下げ・下に、速く・遅く、増や・減ら）。
    # 読み取りのどの道（軸・量・はぐれた符号・単位の無い数・区切りのある数・漢数字の前 など）を通ったかに関係なく数える（QA 002 N1）
    said_signs: tuple[int, ...] = ()
    # 前の発話（答えを待っていた聞き返しのもと）で言った、向きの言葉の全部。答えと合わせたときに said_signs から移して残す。
    # 互いに食い違えば止める。今の向きと1つでも食い違えば、候補にせず止めて確かめる。前の向きを今の向きとして使うことはしない
    earlier_signs: tuple[int, ...] = ()

    @property
    def position(self) -> str | None:
        return self.positions[0] if len(self.positions) == 1 else None

    @property
    def amount(self) -> Amount | None:
        return self.amounts[0] if len(self.amounts) == 1 and not self.unclear_amounts else None

    @property
    def axis(self) -> str | None:
        return self.axes[0] if len(self.axes) == 1 else None

    @property
    def op(self) -> str | None:
        ops = [k for k in self.kinds if k in OPS]
        return ops[0] if len(ops) == 1 else None

    def requested_value(self) -> float | None:
        """利用者が言った、符号つきの量（mm または %）。決まらなければ None。"""
        a = self.amount
        if a is None:
            return None
        if self.op == "speed":
            signs = [s for s in (a.sign, *self.speed_signs) if s is not None]
        else:
            signs = [s for s in (a.sign, *self.axis_signs) if s is not None]
        if len(signs) > 1:
            return None  # 向きの指定が2つ（あいまい）
        if not signs:
            return None  # 符号が分からない（「Y方向に3mm」）。＋を補わない（CHECKER_021 D2・2026-11-03 本人の方針）
        return signs[0] * a.value

    def sign_count(self) -> int:
        """量と方向の言葉についた向き（＋・−）の数。"""
        a = self.amount
        own = [a.sign] if a is not None and a.sign is not None else []
        return len(own) + len(self.speed_signs if self.op == "speed" else self.axis_signs)


# 数字は半角の 0〜9 だけを読む（NFKC のあとも残る ٣ などの数字は、知らない言葉として聞き返す）
_POS_RE = re.compile(r"(?<![A-Z0-9])C\s?([0-9]+)(?![0-9.])")
_NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_SIGN_BEFORE = re.compile(r"(マイナス|プラス|[+\-])\s?$")
_UNIT_AFTER = re.compile(r"^\s?(MM|ミリ|%)")
_OTHER_UNIT_AFTER = re.compile(r"^\s?(センチ|CM|M(?![A-Z])|メートル|パーセント|割|度|倍)")
_KANJI_AMOUNT = re.compile(r"[〇零一二三四五六七八九十百千万]+\s?(ミリ|MM|センチ|CM|%|パーセント|割)")
_VAGUE = re.compile(r"そこそこ|半分|少し|すこし|ちょっと|ちょい|わずか|僅か|倍|もう少し|適当")
# 軸と向き。1つ目の形（案1-a。設計書 docs/DESIGN_NATURAL_DIRECTION_20261105.md は、このブランチには無く、
# ブランチ docs/direction-words-design（59fec81）にある）：「軸 → 符号 → 方向」の順で、間に入ってよいのは
# 「軸」「の」（各1回まで）と空白だけ（「Y-の方向」「Yのマイナス方向」「Y軸マイナス方向」「Yの-の方向」）。
# 2つ目の形（今まで）：「Y-方向」「Y軸」「Yに」「Y+へ」。1つの所は1つの形でだけ数える（同じ符号を2回数えない）。
# 符号の言葉は + − プラス マイナス だけ（長音「ー」・正・負・側・向き・方 は読まない。聞き返す）
_AXIS_RE = re.compile(r"(?<![A-Z])(?P<axis>[XYZ])\s?(?:"
                      r"(?:軸\s?)?(?:の\s?)?(?P<sign_dir>マイナス|プラス|[+\-])\s?(?:の\s?)?(?:軸方向|方向)"
                      r"|(?P<sign>マイナス|プラス|[+\-])?\s?(?:方向|軸|に|へ))")
# 「Xマイナス5ミリ」「Y +3 mm」「X の -5ミリ」：軸のすぐ後（「の」をはさんでもよい）に符号と数字。符号は量の側（_SIGN_BEFORE）で読むので、ここでは軸だけ
_AXIS_SIGNED_NUM = re.compile(r"(?<![A-Z])([XYZ])\s?(?:の\s?)?(?=(?:マイナス|プラス|[+\-])\s?[0-9])")
# 文の終わりの「Zマイナス」「Zのプラス」「Z-」（数字が後に無い）：軸と向き（聞き返しへの答え「Zマイナス」→「3ミリ」のため）
_AXIS_SIGN_END = re.compile(r"(?<![A-Z])([XYZ])\s?(?:の\s?)?(マイナス|プラス|[+\-])(?=[\s、。．.!！]*$)")
# 単独の X・Y・Z（「Yを」「Yの」も、軸を言ったものとして読む。CHECKER_021 D1）
_AXIS_WORD = re.compile(r"(?<![A-Z])([XYZ]{1,3})(?![A-Z])")
# 座標の値の上げ下げ（「Y座標を3mm下げて」「Yの値を3mm減らして」）：その軸の値の増減として読む（2026-11-03 本人の判断）
_COORD_VALUE = re.compile(r"(?<![A-Z])([XYZ])\s?(?:の\s?)?(?:座標|値)")
_VALUE_UP = re.compile(r"上げ|増や|大きく")
_VALUE_DOWN = re.compile(r"下げ|減ら|小さく")
_TOWARD_UP = re.compile(r"(?<!以)(上に|上へ|上方向)")      # 座標の値の話のときも、これは「上へ」（Z）
_TOWARD_DOWN = re.compile(r"(?<!以)(下に|下へ|下方向)")
_UP = re.compile(r"(?<!以)(上げ|上に|上へ|上方向)")
_DOWN = re.compile(r"(?<!以)(下げ|下に|下へ|下方向)")
_SIDE = re.compile(r"(右|左)")
_FRONT_BACK = re.compile(r"(?<!変更)(前|後ろ|手前|奥)(に|へ|方向)(?!戻)")
_SELECT = re.compile(r"選択|選ん|選ぶ|選べ")
_MOVE = re.compile(r"移動|動か|ずら|寄せ|動いて")
_SPEED = re.compile(r"移動速度|速度|速さ|スピード|速く|早く|遅く")
_FASTER = re.compile(r"速く|早く")
_SLOWER = re.compile(r"遅く")
_COMPARE = re.compile(r"比較|比べ|差分|違い")
_UNDO = re.compile(r"元に戻|もとに戻|元へ戻|戻して|取り消|取消|キャンセル|やめて")
_APPROVE = re.compile(
    r"承認|(?<![A-Z])OK(?![A-Z])|オーケー|これでいい|それでいい|決定|確定|書き出|書出|転送|送って|保存"
    r"|(^|[、。,\s])はい([、。,!！\s]|$)")
_START = re.compile(r"起動|スタート|運転開始|動作開始|実行して|再生して")
# 「ここの位置」「そこの点」（唐川さんの実マイク 11/3：「ここの位置を3ミリ下げてくれる」）も、選んでいる位置を指す言葉（11/4 の決定）
_THIS = re.compile(r"ここの位置|ここの点|そこの位置|そこの点|この位置|この点|この命令|ここ|その位置|その点|そこ")

_SIGN = {"+": 1, "プラス": 1, "-": -1, "マイナス": -1}
_SIGN_TOKEN = re.compile(r"マイナス|プラス|[+\-]")


# 音声認識が返す、軸と符号の別の書き方（実マイクで「ワイプラス 3mm」「Y の Plus 3ミリ」と返った）。
# 言った言葉の書き方を揃えるだけで、符号や軸を推測で足さない。カタカナの軸名は、すぐ後に符号・方向・軸・「の」があり、
# すぐ前がカタカナでないときだけ（「ガゼット」「ハワイ」を軸にしない）
_SPOKEN_AXIS = re.compile(r"(?<![ァ-ヺー])(エックス|ワイ|ゼット|ズィー)(?![ァ-ヴー])|(?<![ァ-ヺー])(エックス|ワイ|ゼット|ズィー)(?=プラス|マイナス)")
_SPOKEN_SIGN = re.compile(r"(?<![A-Z])(PLUS|MINUS)(?![A-Z])")


# ひらがなの符号と軸名（唐川さんの実機の試し 11/5：「yぷらす1mm」が「「ぷらす」が分かりませんでした」になった）。
# 書き方を揃えるだけで、符号や軸を補わない。軸名は、すぐ前がひらがなでなく、すぐ後に符号・方向・軸があるときだけ
# （「かわいい」「わいわい」を Y にしない）
_HIRA_SIGN = re.compile(r"ぷらす|まいなす")
_HIRA_AXIS = re.compile(r"(?<![ぁ-ゖ])(えっくす|わい|ぜっと)(?=\s?(?:プラス|マイナス|[+\-]|の\s?(?:プラス|マイナス|[+\-])|方向|軸))")


def _spoken(text: str) -> str:
    text = _HIRA_SIGN.sub(lambda m: {"ぷらす": "プラス", "まいなす": "マイナス"}[m.group(0)], text)
    text = _HIRA_AXIS.sub(lambda m: {"えっくす": "X", "わい": "Y", "ぜっと": "Z"}[m.group(1)], text)
    text = _SPOKEN_AXIS.sub(lambda m: {"エックス": "X", "ワイ": "Y", "ゼット": "Z", "ズィー": "Z"}[m.group(1) or m.group(2)], text)
    return _SPOKEN_SIGN.sub(lambda m: {"PLUS": "プラス", "MINUS": "マイナス"}[m.group(1)], text)


# ---- 読み取りで使わなかった言葉（知らない言葉）----
# 読み取りで使った部分を取り除いたあとに残ってよい、つなぎの言葉（これだけ。短く保つ）。
# 残ってよいのは、決まった場所に、決まった形で1つずつ（下の _run_is_allowed）。同じ言葉を並べた「もも」「でも」は通さない。
# ここに無いもの（カタカナ・漢字・英字・この一覧に無いひらがな）は「知らない言葉」として、移動・速度の指示では聞き返す。
# 迷う言葉は入れない（入れなければ聞き返しになるだけ）。聞き違いを軸に読み替える一覧（「バイオ」→ Y など）は作らない。
# 打ち消しの言葉（NEGATION_WORDS）は、どの一覧にも入れない（tests/test_unknown_words.py で確かめる）。
PARTICLES = ("を", "に", "へ", "の", "で", "は", "が", "と", "も")          # 読んだ言葉のすぐ後に1つだけ
TE_FORMS = ("して", "て")                                                   # 動きの言葉のすぐ後だけ（下げて・移動して）
REQUESTS = ("ください", "お願いします", "おねがいします")                    # 読んだ言葉・助詞・て の後
POLITE_AFTER_TE = ("下さい",)                                               # 「下げて下さい」：て のすぐ後だけ（下（Z）とは読まない）
FINALS = ("ね", "よ")                                                       # 文の終わりだけ。助詞の後はだめ（「はね」）
HESITATIONS = ("えーと", "ええと", "えっと", "あのー", "あの")               # 1つだけで区切られていれば、どこでも
OPENERS = ("それでは", "じゃあ", "では", "まず")                             # 文の初め（読んだ言葉より前）に1つだけで
FILLER_WORDS = PARTICLES + TE_FORMS + REQUESTS + POLITE_AFTER_TE + FINALS + HESITATIONS + OPENERS
# 読み取りで使った動きの言葉のすぐ後の言い終わり（「下げる」「下げます」「ずらす」「移動させて」）。2026-11-03 シクロの判断
# 「ます」「させます」は入れない（CHECKER_030 S30-2。唐川さんの決定 2026-11-03 17:14）：文の終わりの「〜ます」は read_utterance が
# 「〜します」と同じく外して facts.shimasu_text に入れ、2択で確かめる。文の途中の「ます」（「下げます3mm」）は知らない言葉として聞き返す
VERB_ENDINGS = ("させて", "させ", "です", "る", "す")
# 「動かします」「移動します」「ずらします」の「します」。CHECKER_026 M3（唐川さんの判断 2026-11-03 13:27）で、
# 読み取りのつなぎの言葉としては使わない（3b4cac1 の「受け付ける」は取り消し）。文の終わりにあるときだけ read_utterance が外して
# facts.shimasu_text に入れ、pre_check が「変更をご希望ですか？ それとも操作の説明でしょうか？」と確かめる（候補にしない）
SHIMASU_AFTER = ("動か", "移動", "ずら")
# 言い終わりの「〜ます」（CHECKER_030 S30-2）：この言葉のすぐ後の、文の終わりの「ます」だけ。「下げます」→「下げて」と読み、
# 「〜します」と同じ2択にする（候補にしない）。「ません」（打ち消し）・「ますね」「ますです」（重ねた言い終わり）は外さない
MASU_AFTER = ("下げ", "上げ", "さげ", "あげ", "させ", "寄せ")
# 疑問の形のときだけ、元に戻す・比較・選択の言葉の後の「ます」「します」（「元に戻しますか」「比較しますか」）
QUESTION_ONLY_ENDINGS = ("します", "ます")
# 読んだ指示の後の、文の終わりの「です」（「下に3ミリです」「Xのマイナス5ミリです」。2026-11-03 夜）。文の途中・初めでは使わない
SENTENCE_END_COPULA = ("です",)
# 量のすぐ前の「あと」「もう」（「あと3mm下げて」。CHECKER_023A）。「もう少し」はあいまいな量として聞き返す
AMOUNT_PREFIXES = ("あと", "もう")

# 打ち消し・取り消し・言い直しの言葉。移動・速度の指示にあれば、知らない言葉より先に止める（CHECKER_023A B6・B7）
NEGATION_WORDS = ("じゃなく", "じゃない", "ではなく", "でなく", "たくない", "なかっ", "ないで", "なくて", "ない", "なく",
                  "駄目", "だめ", "ダメ", "やめ", "禁止", "違う", "ちがう", "取り消し",
                  "ません", "ずに", "ぬ", "なし", "不要", "ストップ", "中止", "いや", "嫌", "なよ", "んな", "いらない",
                  "いいえ", "止めて", "待って", "結構です", "無し", "無理")
# 一覧のほかに、決まった形で見つける打ち消し：文の終わりの「下げるな」「動かすな」、文の終わりの「下げず」、英字の「NG」
NEGATION_MESSAGE = "{word}などの打ち消しの言葉があるため、変更しません。してほしいことだけを言ってください"


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _fold_kana(text: str) -> str:
    """カタカナをひらがなにする（打ち消しを、書き方によらず見つけるため。「やメて」「ダメ」「ストップ」）。字の数は変えない。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


_UNDO_FOLDED = re.compile(_fold_kana(_UNDO.pattern))


_NEGATION_RE = re.compile(
    f"(?:{_alt(dict.fromkeys(_fold_kana(w) for w in NEGATION_WORDS))})"
    r"|(?<=[るすく])な(?=[\s、。,.!！?？]*$)"   # 文の終わりの「下げるな」「動かすな」
    r"|(?<=[ぁ-ゖ])ず(?=[\s、。,.!！?？]*$)"    # 文の終わりの「下げず」「動かさず」
    r"|(?:(?<![A-Z])|(?<=MM))NG(?![A-Z])")  # 「3mmNG」は大文字にすると「3MMNG」
_P, _TE, _REQ, _FIN = _alt(PARTICLES), _alt(TE_FORMS), _alt(REQUESTS), _alt(FINALS)
# 読んだ言葉の後の1つのひと続き（文の終わりでないとき／文の終わりのとき）
_TAIL_MID = re.compile(f"(?:{_P})|(?:{_P})?(?:{_REQ})|(?:{_TE})(?:{_REQ}|{_alt(POLITE_AFTER_TE)})?")
_TAIL_END = re.compile(f"{_TAIL_MID.pattern}|(?:(?:{_TE})(?:{_REQ}|{_alt(POLITE_AFTER_TE)})?|(?:{_REQ}))?(?:{_FIN})"
                       f"|{_alt(SENTENCE_END_COPULA)}")
_LEAD_STRIP = re.compile(f"{_P}|{_alt(HESITATIONS)}|{_alt(OPENERS)}")
_TAIL_STRIP = re.compile(f"(?:{_P})?(?:{_REQ})?")  # 示すときに後ろから外す助詞など（「バイオを」→「バイオ」）
_ANY_FILLER_SEQ = re.compile(f"(?:{_alt(FILLER_WORDS)})+")
_HESITATION_RE = re.compile(_alt(HESITATIONS))
_OPENER_RE = re.compile(_alt(OPENERS))
# て・して が付いてよい、読んだ動きの言葉の最後の字（下げ・動か・増や・ずら・寄せ・遅く・移動、元に戻・取り消・比べ・比較・選択）
_VERB_END_CHARS = "げかやらせく動戻消べ較択"
# 言葉の区切り（空白・句読点など）。読み取りで使った部分も空白にしてから区切る
_WORD_SEP = re.compile(r"[\s、。,，.．!！?？・／/「」『』（）()…~〜]+")
_WORD = re.compile(r"[^\s、。,，.．!！?？・／/「」『』（）()…~〜]+")
# 「Yのマイナス方向」「Y軸」「X軸方向」：軸のあとの「方向」「軸」（軸を言っていない「方向」「軸」は、知らない言葉として残す）
# 「Y-の方向」「Y軸のマイナス方向」も（_AXIS_RE の1つ目の形と同じ並び。ここは取り除くだけで、符号は数えない）
_AXIS_DIRECTION_WORD = re.compile(r"(?<![A-Z])[XYZ]\s?(?:軸\s?(?=の|マイナス|プラス|[+\-]))?(?:の\s?)?(?:(?:マイナス|プラス|[+\-])\s?(?:の\s?)?)?(?:軸方向|方向|軸)")
# 「Y座標」「Yの値」「Y座標値」：座標の値の言葉（_COORD_VALUE で読んだときだけ取り除く）
_COORD_VALUE_WORD = re.compile(r"(?<![A-Z])[XYZ]\s?(?:の\s?)?(?:座標値|座標|値)")
_CONNECTOR = re.compile(r"そして|それから")  # 2つの変更の区切り（_SPLIT_MOVES）
_VERB_TAIL = re.compile(f"(?<=上げ|下げ|増や|減ら|動か|ずら|寄せ|移動)(?:{_alt(VERB_ENDINGS)})")
_QUESTION_VERB_TAIL = re.compile(f"(?<=戻|消|較|択)(?:{_alt(QUESTION_ONLY_ENDINGS)})")
_AMOUNT_PREFIX = re.compile(f"(?:{_alt(AMOUNT_PREFIXES)})" + r"(?=\s?[0-9]+(?:\.[0-9]+)?\s?(?:MM|ミリ|%))")
# 位置番号のすぐ後の「位置」（「C00005の位置を下に3mm」）。「この位置」「その位置」は _THIS で読む
_POSITION_WORD = re.compile(r"^\s?(?:の\s?)?位置")
# 「ここここ」「そこそこ」：同じ指す言葉の繰り返しは、指す言葉として読まない（そこそこ は、あいまいな量として読む）
_THIS_REPEAT = re.compile(r"(?:ここの位置|ここの点|そこの位置|そこの点|この位置|この点|この命令|ここ|その位置|その点|そこ){2,}")


def _negation_words(text: str, heard: str | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """打ち消しの言葉を、前の動きの言葉ごと取り出す（「下げないで」「駄目」）。取り除くことはしない。

    返す値：(打ち消し, 元に戻す言葉そのものの打ち消し)。「やめて」「取り消して」は元に戻す指示の言葉なので、
    ほかの打ち消しと分ける（移動・速度が無ければ、元に戻す指示として使う）。「元に戻さないで」の「ないで」は打ち消し。
    """
    folded = _fold_kana(text)
    undo = [m.span() for m in _UNDO_FOLDED.finditer(folded)]
    out: list[str] = []
    of_undo: list[str] = []
    for m in _NEGATION_RE.finditer(folded):
        if _inside_katakana_word(text, m.start(), m.end()):
            continue  # 「ワイヤー」の「イヤ」、「ダメージ」の「ダメ」は打ち消しではない
        start = _negation_start(text, m.start())
        word = (heard or text)[start:m.end()]  # 示すのは聞こえたとおりの書き方（「さげません」）
        target = of_undo if any(a < m.end() and m.start() < b for a, b in undo) else out
        if word not in target:
            target.append(word)
    return tuple(out), tuple(of_undo)


_NEGATION_WORD_MAX = 8  # 打ち消しの言葉を示すときに、前へさかのぼる字の数の上限
_NEGATION_LEADS = ("やっぱり", "やはり", "もう")  # 打ち消しを示すときの、言葉のまとまりの初めになる言葉（示すためだけ）


def _negation_start(text: str, i: int) -> int:
    """打ち消し（text[i:] で見つけたもの）を示すときの、言葉のまとまりの始まり。示すためだけ（見つけるかどうかは変えない）。

    前の動きの言葉（ひらがな・漢字）ごと示す（「下げないで」「移動しないで」）。助詞・「とか」の所で止める。
    ただし、文の初め・区切りのすぐ後の「も」「は」などは助詞ではなく言葉の一部として読む（「もういいや」）。
    言葉の途中で切った切れ端（「っぱりいいや」）は示さない：まとまりの始まりまで（上限 8字）さかのぼり、
    それでも始まりに届かなければ、見つけた打ち消しの言葉だけを示す。
    """
    def joins(k: int) -> bool:  # text[k - 1] が、同じ言葉のまとまりの字か
        c = text[k - 1]
        if not ("ぁ" <= c <= "ゖ" or _script(c) == "kanji"):
            return False
        if k >= 2 and text[k - 2:k] == "とか":
            return False
        return not (c in PARTICLES and k >= 2 and _script(text[k - 2]) != "other")

    # 言葉のまとまりの初めになる言葉（「もう」「やっぱり」）。打ち消しの前に、ひらがな・漢字だけでつながっていれば、
    # いちばん近いものから示す（「じゃあもういいや」→「もういいや」、「うーんやっぱりいいや」→「やっぱりいいや」、
    # 「これもう動かさないで」→「もう動かさないで」。QA 001 M2）
    leads = [p for p in range(max(0, i - _NEGATION_WORD_MAX), i)
             if text.startswith(_NEGATION_LEADS, p)
             and all("ぁ" <= c <= "ゖ" or _script(c) == "kanji" for c in text[p:i])]
    if leads:
        return max(leads)
    start = i
    while start > 0 and i - start < _NEGATION_WORD_MAX and joins(start):
        start -= 1
    if start == 0 or not joins(start):
        return start  # まとまりの始まりに届いた
    return i  # 長すぎて始まりに届かない：切れ端を示さず、打ち消しの言葉だけ


def _inside_katakana_word(text: str, start: int, end: int) -> bool:
    """見つけた所が全部カタカナで、前か後ろもカタカナ（長いカタカナの言葉の一部）か。"""
    def kata(i: int) -> bool:
        return 0 <= i < len(text) and _script(text[i]) == "kata"
    return all(kata(i) for i in range(start, end)) and (kata(start - 1) or kata(end))


def _plain_undo(text: str, kinds: set[str]) -> bool:
    """「やめて」「元に戻して」「取り消して」「キャンセル」だけの、元に戻す指示か。

    元に戻す言葉がちょうど1つ、ほかの操作（選択・比較・移動・速度）が無く、「〜のはやめて」「〜はやめて」の形でないとき。
    「キャンセルはやめて」「元に戻すのはやめて」「C00004を選ぶのはやめて」は、元に戻す指示として使わない（止める）。
    """
    folded = _fold_kana(text)
    found = list(_UNDO_FOLDED.finditer(folded))
    if kinds != {"undo"} or len(found) != 1:
        return False
    return not folded[: found[0].start()].rstrip().endswith("は")


def _script(ch: str) -> str:
    if "ぁ" <= ch <= "ゖ":
        return "hira"
    if "ァ" <= ch <= "ヺ" or ch in "ーヽヾ":
        return "kata"
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return "latin"
    if "0" <= ch <= "9":
        return "digit"
    if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" or ch in "々〆":
        return "kanji"
    return "other"


_INVISIBLE = ("Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp", "Mn", "Me")


def _show(word: str) -> str:
    """知らない言葉を、画面で読める形にする。見えない文字（ゼロ幅・制御文字など）だけなら「（見えない文字）」。"""
    visible = "".join(c for c in word if unicodedata.category(c) not in _INVISIBLE)
    return visible if visible.strip() else "（見えない文字）"


def _run_is_allowed(run: str, used_before: bool, used_after: bool, prev_char: str,
                    after_verb_tail: bool = False, after_amount: bool = False) -> bool:
    """読み取りで使わなかった1つのひと続きが、決まった場所の、決まったつなぎの言葉か。"""
    if _HESITATION_RE.fullmatch(run):
        return True
    if after_verb_tail:
        return False  # 「下げます」の後に重ねない（「下げますです」「下げますね」）
    if run in SENTENCE_END_COPULA and not after_amount:
        return False  # 文の終わりの「です」は量のすぐ後だけ（「下に3ミリです」）
    if not used_before:  # 読んだ言葉より前（文の初め）
        return bool(_OPENER_RE.fullmatch(run))
    tail = _TAIL_MID if used_after else _TAIL_END
    m = tail.fullmatch(run)
    if not m:
        return False
    if run.startswith(TE_FORMS) and prev_char not in _VERB_END_CHARS:
        return False  # て・して は動きの言葉のすぐ後だけ
    return True


def _unknown_words(residual: str, used_mask: Sequence[bool], source: str,
                   verb_tail_ends: frozenset[int] = frozenset(), amount_ends: frozenset[int] = frozenset()
                   ) -> tuple[str, ...]:
    """読み取りで使った部分を空白にした文から、知らない言葉を取り出す。

    residual：読み取りで使った部分を空白にした文。used_mask：その字を読み取りで使ったか。source：空白にする前の文。
    区切りと使った部分で分けたひと続きごとに、決まった場所のつなぎの言葉（_run_is_allowed）でなければ知らない言葉にする。
    """
    out: list[str] = []
    for m in _WORD.finditer(residual):
        run = m.group(0)
        before = any(used_mask[: m.start()])
        after = any(used_mask[m.end():])
        prev = source[m.start() - 1] if m.start() > 0 else ""
        j = m.start()  # 前の読んだ言葉の終わり（間の空白は飛ばす：「3mm です」「下げます ね」）
        while j > 0 and source[j - 1].isspace() and not used_mask[j - 1]:
            j -= 1
        if _run_is_allowed(run, before, after, prev, j in verb_tail_ends, j in amount_ends):
            continue
        # 示すときは、後ろの助詞などを外す（「バイオを」→「バイオ」）。外すと何も残らないときは、そのまま示す
        # 前の助詞・つなぎの言葉も外す（「をバイオ」→「バイオ」。ひらがなの言葉の途中では分けない）。
        # つなぎの言葉を並べただけのもの（「えーとえーと」「あのあの」）は、分けずにそのまま示す
        word = run if _ANY_FILLER_SEQ.fullmatch(run) else next(
            (run[:k] for k in range(1, len(run)) if _TAIL_STRIP.fullmatch(run[k:])
             and not _ANY_FILLER_SEQ.fullmatch(run[:k])), run)
        word = next((word[k:] for k in range(1, len(word)) if _LEAD_STRIP.fullmatch(word[:k])
                     and _script(word[k]) != "hira" and not _ANY_FILLER_SEQ.fullmatch(word[k:])), word)
        word = _show(word)
        if word not in out:
            out.append(word)
    return tuple(out)


# ひらがなの「さげ」「あげ」は「下げ」「上げ」と同じ動きの言葉として読む（向きは言葉だけから。軸は足さない）。
# 前がひらがなのときは、助詞のあとだけ（「ささげて」を読まない）
_HIRA_VERB = re.compile(r"(?:(?<![ぁ-ゖ])|(?<=[をにへのではがとも]))(さげ|あげ)")
# 疑問の形：文の終わりの ？・?、ひらがなのすぐ後の「か」（「下げますか」「3mmですか」）
QUESTION_MARKS = "?？﹖︖⁇‽❓❔⸮"


def _is_question_mark(c: str) -> bool:
    return c in QUESTION_MARKS or "?" in unicodedata.normalize("NFKC", c)


_QUESTION_END = re.compile(f"(?:(?<=[ぁ-ゖ])か)?[{re.escape(QUESTION_MARKS)}]*[\\s　。．.!！]*$")
# 繰り返し・往復（2026-11-03 夜）。「2往復」「2回の移動」ははっきりしたもの。「2回」「行き来」「繰り返し」はどちらか分からない
_ROUND_TRIP = re.compile(r"(?:([0-9]+)\s?)?往復")
_REPEAT_TIMES = re.compile(r"([0-9]+)\s?回の移動|繰り返しの移動")
# 「回転」「回目」は繰り返しではない。回数は2以上だけ（「1回」「もう1回」は知らない言葉として聞き返す）
_REPEAT_AMBIGUOUS = re.compile(r"([0-9]+)\s?回(?!転|目)|行き来|繰り返し|繰り返|くりかえし|くりかえ")
# 回数の変更（feat/repeat-count）と食い違わない文にする（QA P1-1）。往復・範囲の決まらない繰り返しは今どおり止める
REPEAT_STOP_MESSAGE = ("往復や、範囲の決まらない繰り返しは、このアプリでは扱いません。"
                       "回数を変えるときは「C00001からC00003の作業を2回から3回にして」のように、範囲を言ってください")
# 場所の言葉（2026-11-03 夜）。どの位置かは決めない
PLACE_WORDS = ("置くところ", "置き場所", "置く位置", "取るところ", "つかむところ", "離すところ")
_PLACE = re.compile(_alt(PLACE_WORDS))
# 向きの言葉（軸には当てない。手前・前・後ろ・奥は止め、右・左は聞き返す）
_RELATIVE_DIR = re.compile(r"手前|後ろ|奥|(?<!変更)(?<!以)(?<!名)前|右|左")


def _repeat(text: str) -> tuple[tuple[int | None, str] | None, list[tuple[int, int]]]:
    """繰り返し・往復の言葉を読む。(回数, 種類) と、読んだ部分の位置。"""
    def counted(m: re.Match) -> bool:
        return not (m.re is not _ROUND_TRIP and m.group(1) and int(m.group(1)) < 2)

    pats = (_ROUND_TRIP, _REPEAT_TIMES, _REPEAT_AMBIGUOUS)
    for pat, kind in zip(pats, ("round_trip", "times", "ambiguous")):
        found = [m for m in pat.finditer(text) if counted(m)]
        if found:
            n = next((int(m.group(1)) for m in found if m.group(1)), None)
            spans = [m.span() for p2 in pats for m in p2.finditer(text) if counted(m)]
            return (n, kind), spans
    return None, []


# 文の終わりの「動かします」「移動します」「ずらします」（後ろは句読点・空白だけ）。CHECKER_026 M3
# CHECKER_032 P1-1（唐川さん 11/3 夜）：言い終わりの「ね」「よ」（「下げますね」）・「〜ておきます」・「量＋にします」も、
# 既定の確認ルール（11/3 17:14）どおり2択で確かめる（候補にしない）。「ません」「ました」「ましょう」は外さない（今まで通り）
MAX_AMOUNT_DECIMALS = 6
NOT_UNIT_NOTE = "（単位が mm・ミリ・% ではありません）"
# 単位の無い数（「Z+3」の 3）の書き方。_amount_check が単位を聞き返す（facts の文は今までと同じ）
_NO_UNIT_ITEM = re.compile(r"「([0-9]+(?:\.[0-9]+)?)」" + re.escape(NOT_UNIT_NOTE))
_SHIMASU_END = re.compile(rf"(?<={'|'.join(SHIMASU_AFTER)})(?:しておき|し)ます[ねよ]?[\s　。．.!！]*$")
_SHIMASU_VERB = re.compile(f"(?:{'|'.join(SHIMASU_AFTER)})$")
_MASU_END = re.compile(rf"(?<={'|'.join(MASU_AFTER)})(?:ておき)?ます[ねよ]?[\s　。．.!！]*$")
# 「下げてくれる」「下げてもらえる」（唐川さんの実マイク 11/3）：お願いとも、できるかの質問とも読める。
# 「〜ます」と同じく外して2択で確かめる（候補にしない。安全の決まりは緩めない）。「くれない」「もらえない」は外さない
_KURERU_END = re.compile(r"(?<=て)(?:くれる|くれます|もらえる|もらえます)[ねよ]?[\s　。．.!！]*$")
_NI_SHIMASU_END = re.compile(r"([0-9０-９]+(?:[.．][0-9０-９]+)?\s*(?:mm|ＭＭ|MM|ｍｍ|ミリ))\s*に(?:しておき|し)ます[ねよ]?[\s　。．.!！]*$", re.I)


def _strip_shimasu(text: str) -> str | None:
    """「3mm下げて動かします」→「3mm下げて」、「下に3mm移動します」→「下に3mm移動して」。文の終わりが「〜します」「〜ます」でなければ None。

    すぐ前が「て」の動きの言葉（「下げて動かします」）なら、後ろの「動かします」ごと外す。それ以外は「します」を「して」にする。
    言葉は足さない・変えない（外した文は、ふつうの指示と同じ検査を通る）。
    """
    k = _KURERU_END.search(text)
    if k is not None:
        return text[: k.start()]  # 「ここの位置を3ミリ下げてくれる」→「ここの位置を3ミリ下げて」（言葉は足さない）
    n = _NI_SHIMASU_END.search(text)
    if n is not None:
        return text[: n.end(1)]  # 「Zマイナス3ミリにします」→「Zマイナス3ミリ」（言葉は足さない）
    m = _SHIMASU_END.search(text)
    if m is None:
        m = _MASU_END.search(text)
        # 「3mm下げます」→「3mm下げて」、「Yのマイナス3ミリ移動させます」→「…移動させて」（CHECKER_030 S30-2）
        return None if m is None else text[: m.start()] + "て"
    head = text[: m.start()]
    verb = _SHIMASU_VERB.search(head)
    before = head[: verb.start()].rstrip()
    if before.endswith("て") and len(before) > 1:
        return before
    return head + "して"


def read_utterance(raw: str) -> UtteranceFacts:
    # Whole-utterance cancellation, excluding negated or compound operations.
    if re.fullmatch(r"今の提案は取り消して[\s。.!！]*", unicodedata.normalize("NFKC", raw).strip()):
        return replace(_read("取り消して", question=False), raw=raw)
    q = _QUESTION_END.search(raw)
    tail = q.group(0) if q else ""
    at_end = "か" in tail or any(_is_question_mark(c) for c in tail)
    plain = raw[: q.start()].rstrip() if at_end else raw
    if any(_is_question_mark(c) for c in plain):
        # 文の途中の疑問符（「3mm？下げて」）：黙って捨てない。疑問として、選択肢なしで言い直してもらう
        f = _read("".join(" " if _is_question_mark(c) else c for c in plain), question=True)
        return replace(f, raw=raw, question=True, question_text=None)
    if at_end:
        # 疑問の形：？・か を外した文を読み、確かめるために印を付ける（候補にはしない。pre_check が確かめる）。
        # 「3mm下げて動かしますか？」は「〜します」も外した「3mm下げて」で確かめる（CHECKER_026 M3。疑問の扱いに合わせる）
        plain = _strip_shimasu(plain) or plain
        f = _read(plain, question=True)
        return replace(f, raw=raw, question=True, question_text=plain or None)
    request = _strip_shimasu(raw)
    if request is not None:
        # 言い終わりの「〜します」（CHECKER_026 M3）：外した文を読み、候補にせず確かめる印を付ける（pre_check が確かめる）
        return replace(_read(request, question=False), raw=raw, shimasu_text=request)
    return _read(raw, question=False)


def _read(raw: str, question: bool) -> UtteranceFacts:
    heard = _spoken(normalize(raw))  # 聞こえたとおりの書き方（示すときに使う）
    text = _HIRA_VERB.sub(lambda m: {"さげ": "下げ", "あげ": "上げ"}[m.group(1)], heard)  # 字の数は変えない
    # 位置番号（数字を量と読まないように、先に取り除く）
    positions: list[str] = []
    bad_positions: list[str] = []
    masked = list(text)
    for m in _POS_RE.finditer(text):
        digits = m.group(1)
        if len(digits) == 5:
            name = f"C{digits}"
            if name not in positions:
                positions.append(name)
        else:
            bad_positions.append(m.group(0))
        for i in range(m.start(), m.end()):
            masked[i] = " "
    rest = "".join(masked)

    # 量
    amounts: list[Amount] = []
    unclear: list[str] = []
    unclear_signs: list[int] = []  # 単位の無い・違う数のすぐ前の符号（「Z+3」の +）。はぐれた符号ではなく、量の側の符号として残す
    for m in _NUM_RE.finditer(rest):
        before = rest[: m.start()]
        after = rest[m.end():]
        if before.endswith((".", ",")) or (before and "0" <= before[-1] <= "9") or after.startswith((",", ".")):
            unclear.append(f"「{m.group(0)}」（区切りのある数字）")
            continue
        if "." in m.group(1) and len(m.group(1).split(".", 1)[1]) > MAX_AMOUNT_DECIMALS:
            # 細かすぎる小数（float の誤差と区別できない）：黙って丸めず聞き返す（内部 Safety ad77d67 P2）
            unclear.append(f"「{m.group(0)}」（小数が細かすぎます。小数点以下 {MAX_AMOUNT_DECIMALS}けたまで）")
            continue
        unit = _UNIT_AFTER.match(after)
        if unit is None:
            other = _OTHER_UNIT_AFTER.match(after)
            word = other.group(1) if other else ""
            unclear.append(f"「{m.group(0)}{word}」{NOT_UNIT_NOTE}")
            # 「Z+3」「Zのマイナス5」：符号は数に付いている（足りないのは単位）。符号の付く所ではなく、単位を聞き返す。
            # 量は作らない（単位を補わない）ので、この符号は向きとしても使わない
            us = _SIGN_BEFORE.search(before)
            if us is not None:
                unclear_signs.append(_SIGN[us.group(1)])
            continue
        sm = _SIGN_BEFORE.search(before)
        amounts.append(Amount(
            value=float(m.group(1)),
            unit="%" if unit.group(1) == "%" else "mm",
            sign=_SIGN[sm.group(1)] if sm else None,
            text=(sm.group(0) if sm else "") + m.group(0) + unit.group(0),
        ))
    for m in _KANJI_AMOUNT.finditer(rest):
        unclear.append(f"「{m.group(0)}」（漢数字）")
    for m in _VAGUE.finditer(rest):
        unclear.append(f"「{m.group(0)}」（量がはっきりしません）")

    is_speed = bool(_SPEED.search(rest))
    rest_no_speed = _SPEED.sub(" ", rest)

    # 方向
    axes: list[str] = []
    axis_signs: list[int] = []
    placed_axis_signs = 0  # 軸に付いた符号の数（「X-方向」の -）
    for m in _AXIS_RE.finditer(rest_no_speed):
        if m.group("axis") not in axes:
            axes.append(m.group("axis"))
        sign = m.group("sign_dir") or m.group("sign")
        if sign:
            axis_signs.append(_SIGN[sign])
            placed_axis_signs += 1
    for m in _AXIS_SIGNED_NUM.finditer(rest_no_speed):
        if m.group(1) not in axes:
            axes.append(m.group(1))
    if not is_speed:
        for m in _AXIS_SIGN_END.finditer(rest_no_speed):
            if not _NUM_RE.search(rest_no_speed[m.end():]):
                if m.group(1) not in axes:
                    axes.append(m.group(1))
                axis_signs.append(_SIGN[m.group(2)])
                placed_axis_signs += 1
    speed_signs: list[int] = []
    explicit: list[str] = list(axes)  # ここまでは軸の文字で言った軸
    up, down = bool(_UP.search(rest)), bool(_DOWN.search(rest))
    coord = [] if is_speed else list(dict.fromkeys(m.group(1) for m in _COORD_VALUE.finditer(rest_no_speed)))
    vertical = False  # 「上げ・下げ・上に・下に」を Z（上下）として読んだ
    if is_speed:
        speed_signs += [1] * up + [-1] * down
        speed_signs += [1] * bool(_FASTER.search(rest)) + [-1] * bool(_SLOWER.search(rest))
    elif coord:
        # 座標の値の増減：その軸に、増やす＋・減らす−。「上に・下に」は別に Z（上下）として読む（軸が2つなら聞き返す）
        for a in coord:
            if a not in axes:
                axes.append(a)
            if a not in explicit:
                explicit.append(a)
        axis_signs += [1] * bool(_VALUE_UP.search(rest_no_speed)) + [-1] * bool(_VALUE_DOWN.search(rest_no_speed))
        t_up, t_down = bool(_TOWARD_UP.search(rest)), bool(_TOWARD_DOWN.search(rest))
        if t_up or t_down:
            vertical = True
            if "Z" not in axes:
                axes.append("Z")
            axis_signs += [1] * t_up + [-1] * t_down
    elif up or down:
        vertical = True
        if "Z" not in axes:
            axes.append("Z")
        axis_signs += [1] * up + [-1] * down
    # 単独の X・Y・Z も軸として足す（「Yを3mm下げて」は Y と 下げ（Z）の2つ → 聞き返す）
    if not is_speed:
        for m in _AXIS_WORD.finditer(rest_no_speed):
            for a in m.group(1):  # 「XY」は X と Y
                if a not in axes:
                    axes.append(a)
                if a not in explicit:
                    explicit.append(a)
    sides = tuple(dict.fromkeys(_SIDE.findall(rest)))

    kinds: set[str] = set()
    if _SELECT.search(rest):
        kinds.add("select")
    if is_speed:
        kinds.add("speed")
    if _MOVE.search(rest_no_speed) or axes or sides:
        kinds.add("move")
    if _COMPARE.search(rest):
        kinds.add("compare")
    if _UNDO.search(rest):
        kinds.add("undo")
    stops: list[str] = []
    if _APPROVE.search(rest):
        kinds.add("approve")
        stops.append(APPROVE_MESSAGE)
    if _START.search(rest):
        kinds.add("start")
        stops.append(START_MESSAGE)
    if _FRONT_BACK.search(rest_no_speed):
        kinds.add("move")
        stops.append(FRONT_BACK_MESSAGE)

    # 読み取りで使った部分を空白にして、残った言葉を調べる（その文の読み方で使ったものだけを取り除く）
    used = list(rest)

    def blank(start: int, end: int) -> None:
        for i in range(start, end):
            used[i] = " "

    amount_ends: set[int] = set()
    for m in _NUM_RE.finditer(rest):
        end = m.end()
        unit = _UNIT_AFTER.match(rest[end:]) or _OTHER_UNIT_AFTER.match(rest[end:])
        blank(m.start(), end + (unit.end() if unit else 0))
        if unit is not None and _UNIT_AFTER.match(rest[end:]):
            amount_ends.add(end + unit.end())
    # 「ここここ」のような繰り返しでない「この位置」「ここ」だけを、指す言葉として取り除く
    repeats = [m.span() for m in _THIS_REPEAT.finditer(rest)]
    for m in _THIS.finditer(rest):
        if not any(a <= m.start() < b for a, b in repeats):
            blank(m.start(), m.end())
    used_patterns = [_SIGN_TOKEN, _KANJI_AMOUNT, _VAGUE, _UP, _DOWN, _SIDE, _FRONT_BACK, _SELECT, _MOVE,
                     _COMPARE, _UNDO, _APPROVE, _START, _CONNECTOR, _AMOUNT_PREFIX]
    if is_speed:
        used_patterns += [_SPEED]
    else:
        # 速度の指示では、軸の言葉は読まない（使わない言葉として残す）
        used_patterns += [_AXIS_RE, _AXIS_SIGNED_NUM, _AXIS_SIGN_END, _AXIS_WORD, _AXIS_DIRECTION_WORD, _TOWARD_UP, _TOWARD_DOWN]
        if coord:
            used_patterns += [_COORD_VALUE_WORD, _VALUE_UP, _VALUE_DOWN]
    for pat in used_patterns:
        for m in pat.finditer(rest):
            blank(m.start(), m.end())
    verb_tail_ends: set[int] = set()
    for m in list(_VERB_TAIL.finditer(rest)) + (list(_QUESTION_VERB_TAIL.finditer(rest)) if question else []):
        if used[m.start() - 1] == " ":  # 前の動きの言葉を読んだときだけ
            blank(m.start(), m.end())
            verb_tail_ends.add(m.end())
    for m in _POS_RE.finditer(text):
        if len(m.group(1)) == 5:
            w = _POSITION_WORD.match(text[m.end():])
            if w:
                blank(m.end(), m.end() + w.end())
    repeat, repeat_spans = _repeat(rest)
    for a, b in repeat_spans:
        blank(a, b)
    place = _PLACE.search(rest)
    for m in _PLACE.finditer(rest):
        blank(m.start(), m.end())
    # 場所の言葉を行き先に使った（「置くところに」「置き場所へ」「置く位置まで」）
    dest = re.match(r"\s?(?:まで|に|へ)", rest[place.end():]) if place else None
    place_dest = dest is not None
    if dest:
        blank(place.end(), place.end() + dest.end())  # 行き先の「に・へ・まで」は読んだ（知らない言葉にしない。下で断る）
    negations, undo_negations = _negation_words(text, heard)
    if undo_negations and _plain_undo(text, kinds):
        undo_negations = ()  # 元に戻す指示の言葉そのもの（「やめて」「取り消して」）は打ち消しにしない
    used_mask = [used[i] == " " and not text[i].isspace() for i in range(len(text))]
    residual = "".join(" " if used[i] == " " else heard[i] for i in range(len(text)))  # 示すのは聞こえたとおり
    unknown = _unknown_words(residual, used_mask, text, frozenset(verb_tail_ends), frozenset(amount_ends))

    return UtteranceFacts(
        raw=raw, text=text,
        kinds=tuple(k for k in KIND_ORDER if k in kinds),
        positions=tuple(positions), bad_positions=tuple(bad_positions),
        amounts=tuple(amounts), unclear_amounts=tuple(unclear),
        axes=tuple(axes), axis_signs=tuple(axis_signs), sides=sides,
        speed_signs=tuple(speed_signs),
        refers_to_selection=bool(_THIS.search(rest)),
        stray_signs=(len(_SIGN_TOKEN.findall(rest)) - sum(a.sign is not None for a in amounts) - placed_axis_signs
                     - len(unclear_signs)),
        unclear_signs=tuple(unclear_signs),
        said_signs=_said_signs(rest, rest_no_speed, bool(coord)),
        vertical=vertical,
        explicit_axes=tuple(a for a in axes if a in explicit),
        value_axes=tuple(coord),
        stop_reasons=tuple(stops),
        unknown_words=unknown,
        negations=negations, undo_negations=undo_negations,
        repeat=repeat, place_ref=place.group(0) if place else None, place_as_destination=place_dest,
        relative_dir=tuple(dict.fromkeys(m.group(0) for m in _RELATIVE_DIR.finditer(rest))),
    )


def _said_signs(rest: str, rest_no_speed: str, coord: bool) -> tuple[int, ...]:
    """発話に出た向きの言葉の全部（読んだ道に関係なく）。示すためではなく、答えと合わせたときの食い違いを見つけるため。"""
    out = [_SIGN[t] for t in _SIGN_TOKEN.findall(rest)]
    out += [1] * len(_UP.findall(rest)) + [-1] * len(_DOWN.findall(rest))
    out += [1] * len(_FASTER.findall(rest)) + [-1] * len(_SLOWER.findall(rest))
    if coord:
        out += [1] * len(_VALUE_UP.findall(rest_no_speed)) + [-1] * len(_VALUE_DOWN.findall(rest_no_speed))
    return tuple(out)


def _carried_signs(previous: UtteranceFacts) -> tuple[int, ...]:
    """答えと合わせるときに残す、前の発話の向きの言葉（前に残したものと、前の発話そのものの言葉）。"""
    return tuple(dict.fromkeys(previous.earlier_signs + previous.said_signs))


def signs_conflict(previous: UtteranceFacts, answer: UtteranceFacts) -> bool:
    """聞き返しのもとの発話で言った向きの言葉と、新しい指示（ボタンの文・全部の言い直し）の向きの言葉が食い違うか（Safety 002 Medium-1）。"""
    before = set(_carried_signs(previous))
    now = set(answer.said_signs)
    return bool(before and now and (len(before) > 1 or now - before))


def _merged_extras(previous: UtteranceFacts, answer: UtteranceFacts) -> dict:
    """答えと合わせるときの、疑問・繰り返し・場所・向きの欄。どちらかにあれば残す。
    疑問の形が混ざったときは、確かめる選択肢の文（question_text）を作らない（言い直してもらう）。"""
    return dict(
        question=previous.question or answer.question, question_text=None,
        repeat=answer.repeat or previous.repeat,
        place_ref=answer.place_ref or previous.place_ref,
        place_as_destination=answer.place_as_destination or previous.place_as_destination,
        relative_dir=tuple(dict.fromkeys(previous.relative_dir + answer.relative_dir)),
    )


def combine_axes(previous: UtteranceFacts, answer: UtteranceFacts) -> UtteranceFacts:
    """量を聞き返した後に、上げ・下げだけの答え（「3mm下げて」）が来たとき：前の軸と合わせる（推測で Z だけにしない）。"""
    return replace(answer, raw=f"{previous.raw} ／ {answer.raw}", text=f"{previous.text} ／ {answer.text}",
                   axes=tuple(dict.fromkeys(previous.axes + answer.axes)),
                   explicit_axes=tuple(dict.fromkeys(previous.explicit_axes + answer.explicit_axes)),
                   value_axes=tuple(dict.fromkeys(previous.value_axes + answer.value_axes)),
                   vertical=previous.vertical or answer.vertical,
                   positions=answer.positions or previous.positions,
                   refers_to_selection=previous.refers_to_selection or answer.refers_to_selection,
                   unknown_words=tuple(dict.fromkeys(previous.unknown_words + answer.unknown_words)),
                   negations=tuple(dict.fromkeys(previous.negations + answer.negations)),
                   undo_negations=tuple(dict.fromkeys(previous.undo_negations + answer.undo_negations)),
                   # 答えの「〜します」を外した文だけで［変更したい］を作らない（前に言った軸を捨てることになる）。今どおり聞き返す
                   shimasu_text=None,
                   # 前に言った向きの言葉は全部残す（QA 002 N1）
                   earlier_signs=tuple(dict.fromkeys(_carried_signs(previous) + answer.earlier_signs)),
                   **_merged_extras(previous, answer))


def is_explicit_cancellation(text: str) -> bool:
    """Whole cancellation requests only; a mention or uncertainty is not consent."""
    t = unicodedata.normalize("NFKC", text).strip()
    return bool(re.fullmatch(
        r"(?:(?:やっぱり|やはり|じゃあ|では)[\s、]*|(?:今の|この)提案[はを])?"
        r"(?:取り消(?:して|します)|取消して|キャンセル(?:して)?|元に戻(?:して|します)|もとに戻して|やめて)"
        r"(?:ください)?[\s。.!！]*|さっきのを戻して[\s。.!！]*", t))


def is_explicit_direction_answer(previous: UtteranceFacts, answer: UtteranceFacts) -> bool:
    """Axis/sign alone answering a missing direction for an explicitly pinned move.

    New instructions, positions, amounts and physical directions cannot borrow
    the previous amount. Session still applies every existing pre_check.
    """
    return bool(previous.op == "move" and previous.position and previous.amount is not None
                and not previous.axes and not previous.sides and not previous.unclear_amounts
                and answer.kinds == ("move",) and len(answer.explicit_axes) == 1
                and len(answer.axis_signs) == 1 and not answer.vertical
                and not answer.question and answer.shimasu_text is None
                and re.fullmatch(r"[XYZ](?:の)?(?:プラス|マイナス|[+\-])(?:方向)?[。.!！]*", answer.text.strip()))


def is_sign_only_answer(answer: UtteranceFacts) -> bool:
    """聞き返し「プラスですか、マイナスですか？」への答えになりうる、符号の言葉1つだけの発話（「マイナス」「マイナスで」）。

    疑問・ためらい・打ち消し・知らない言葉・量・軸・位置・上下・右左が1つでもあれば答えにしない（C1。11/5）。
    答えとして足したあとも pre_check からの検査をすべて通る。
    """
    return bool(not answer.kinds and not answer.axes and not answer.positions and not answer.bad_positions
                and not answer.amounts and not answer.unclear_amounts and answer.stray_signs == 1
                and not answer.unknown_words and not answer.negations and not answer.undo_negations
                and not answer.question and not answer.stop_reasons and answer.shimasu_text is None
                and not answer.vertical and not answer.sides and not answer.speed_signs and not answer.place_ref)


def is_direction_answer(previous: UtteranceFacts, answer: UtteranceFacts, selected: str | None) -> bool:
    """方向を聞き返したあとの「Yマイナス」（軸と符号だけ）を、前に言った量に足してよいか（C2。11/5）。

    足すのは次のどちらかだけ：
    - 前の発話に軸が無く、位置が決まっている（口で言った位置、または画面で選んでいる位置。選び直すと聞き返しは捨てる）
    - 前の発話の軸が1つで、答えの軸と同じ（「Yの方向に10mm」→「Yマイナス」）。違う軸なら足さない
    前の量に符号があるとき・あいまいな量・上下・右左があるときは足さない。答えに量・位置・ためらい・疑問があれば足さない。

    ただし、聞き返しのもとの発話で言った向きの言葉と、新しい指示（ボタンの文・全部の言い直し）の向きの言葉が食い違うときは、
    前の発話と合わせる（真を返す）。合わせた事実は pre_check が食い違いとして止めて確かめる（候補にしない。Safety 002 Medium-1）。
    """
    if (previous.op in ("move", "speed") and answer.op == previous.op and signs_conflict(previous, answer)):
        return True
    if not (previous.op == "move" and previous.amount is not None and previous.amount.sign is None
            and not previous.unclear_amounts and not previous.axis_signs and not previous.sides
            and not previous.vertical and not previous.speed_signs):
        return False
    if not (answer.kinds == ("move",) and len(answer.explicit_axes) == 1 and len(answer.axis_signs) == 1
            and not answer.amounts and not answer.unclear_amounts and not answer.positions and not answer.bad_positions
            and not answer.vertical and not answer.sides and not answer.question and answer.shimasu_text is None
            and not answer.unknown_words and not answer.negations and not answer.undo_negations
            and not answer.stop_reasons and answer.stray_signs == 0
            and re.fullmatch(r"[XYZ](?:の)?(?:プラス|マイナス|[+\-])(?:方向)?[。.!！]*", answer.text.strip())):
        return False
    if not previous.axes:
        return previous.position is not None or selected is not None
    return len(previous.axes) == 1 and previous.axes == answer.axes


def merge_answer(previous: UtteranceFacts, answer: UtteranceFacts) -> UtteranceFacts:
    """聞き返しへの答え（操作の言葉が無い1文）を、前の発話で分かっていたことに足す。

    前の発話の事実（replace で全部の項目）から始めて、答えで変わる項目だけを書き換える。新しい項目を足しても、
    黙って落とさない（CHECKER_026 再修正 P0-1：vertical を落として「下げて」→「3ミリ」が Z− になった）。
    tests/test_c026_r2.py の test_p0_merge_answer_drops_no_field で、全部の項目を確かめる。
    """
    has_amount_info = bool(answer.amounts or answer.unclear_amounts)
    loose = _SIGN_TOKEN.findall(answer.text)
    if (not has_amount_info and not answer.axes and len(loose) == 1 and answer.stray_signs == 1
            and previous.axes and not previous.axis_signs and previous.amount is not None
            and previous.amount.sign is None):
        # 「プラスですか、マイナスですか？」への答え「マイナス」：前の発話の軸の向きにする（軸は前の発話のまま）
        answer = replace(answer, axes=previous.axes, explicit_axes=previous.explicit_axes,
                         vertical=previous.vertical, axis_signs=(_SIGN[loose[0]],), stray_signs=0)
    return replace(
        previous,
        raw=f"{previous.raw} ／ {answer.raw}",
        text=f"{previous.text} ／ {answer.text}",
        positions=answer.positions or previous.positions,
        bad_positions=answer.bad_positions if answer.positions or answer.bad_positions else previous.bad_positions,
        amounts=answer.amounts if has_amount_info else previous.amounts,
        unclear_amounts=answer.unclear_amounts if has_amount_info else previous.unclear_amounts,
        axes=answer.axes or previous.axes,
        axis_signs=answer.axis_signs if answer.axes else previous.axis_signs,
        # 軸の文字で言った軸・上下から補った印は、どちらかにあれば残す（上下から補った Z を、確かめずに使わない）
        explicit_axes=tuple(dict.fromkeys(previous.explicit_axes + answer.explicit_axes)),
        value_axes=tuple(dict.fromkeys(previous.value_axes + answer.value_axes)),
        vertical=previous.vertical or answer.vertical,
        sides=answer.sides or previous.sides,
        speed_signs=answer.speed_signs or previous.speed_signs,
        refers_to_selection=previous.refers_to_selection or answer.refers_to_selection,
        stray_signs=answer.stray_signs if (has_amount_info or answer.axes) else previous.stray_signs,
        stop_reasons=tuple(dict.fromkeys(previous.stop_reasons + answer.stop_reasons)),
        # 答えの中の知らない言葉も、前の発話の知らない言葉も捨てない（どちらかにあれば聞き返す）
        unknown_words=tuple(dict.fromkeys(previous.unknown_words + answer.unknown_words)),
        negations=tuple(dict.fromkeys(previous.negations + answer.negations)),
        undo_negations=tuple(dict.fromkeys(previous.undo_negations + answer.undo_negations)),
        # 答えと合わせた文から［変更したい］の2択は作らない（ボタンの文は1つの発話から作る）
        shimasu_text=None,
        unclear_signs=answer.unclear_signs if has_amount_info else previous.unclear_signs,
        # 前の発話の向きの言葉は、読み取りのどの道を通ったかに関係なく、全部 earlier_signs に残す（QA 002 N1）。
        # said_signs は答えの発話の言葉だけ（次に合わせるときに、また earlier_signs に移す）
        said_signs=answer.said_signs,
        earlier_signs=_carried_signs(previous),
        **_merged_extras(previous, answer),
    )


# ---- 判定 ----
@dataclass(frozen=True)
class Verdict:
    status: str   # "ok" / "ask"（聞き返す） / "stop"（止める）
    message: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # 聞き返しの短い選択肢（見せる文, 指示の文）。押すと、その文を指示として送る
    # 答えを待つ形の質問（「何mm動かしますか？」「どの方向に…？」など。ふつうの指示なら、次の答えを前の発話に足す）。
    # 「〜します」の発話には答えを足さないので、_shimasu_check がこの印を見て、言い直してもらう文に変える（QA P1-A）
    needs_answer: bool = False


# 「上・下 ＝ Z±」は、上下の向きの設定（voice_teaching/updown.py）の照合に通ったときだけ使う（CHECKER_026 M2・11章）。
# 照合の結果（updown.UpdownStatus）は呼ぶ側（Session）が毎回渡す。渡さなければ「未確認」として扱う（止める側）。
# 未確認・不一致・変更済みのときは、上・下を Z として読まず、状態ごとの案内で聞き返す。選択肢にも［下方向へ …］を出さない。
# 軸と符号を明示した指示（「Zマイナス3ミリ」）と、軸の座標の値の増減（「Z座標を3mm下げて」＝値を減らす）は、
# 上下の向きに依存しないので、どの状態でも今どおり読む。
UPDOWN_UNSET_MESSAGE = ud.MSG_UNCONFIRMED


def _updown(updown: "ud.UpdownStatus | None") -> "ud.UpdownStatus":
    return updown if isinstance(updown, ud.UpdownStatus) else ud.NOT_CONFIGURED


def direction_names(updown: "ud.UpdownStatus | None" = None) -> dict:
    """確認済みの方向の名前（軸 → 向き → 名前）。上下の向きの照合に通ったときだけ Z の 上・下。
    右・左（実機の確認まで未定）、前・後（止める）は入れない（推測で足さない）。"""
    return _updown(updown).direction_names


OK = Verdict("ok")


def _ask(msg: str, choices: tuple[tuple[str, str], ...] = ()) -> Verdict:
    return Verdict("ask", msg, choices)


def _ask_slot(msg: str, choices: tuple[tuple[str, str], ...] = ()) -> Verdict:
    """量・方向・向きが足りない・はっきりしないときの、答えを待つ形の質問。"""
    return Verdict("ask", msg, choices, needs_answer=True)


def _num(x: float) -> str:
    return f"{x:g}"


def _where(facts: UtteranceFacts) -> str:
    return f"{facts.position}を" if facts.position else ""


def _stop(msg: str) -> Verdict:
    return Verdict("stop", msg)


def unknown_words_message(words: Sequence[str], op: str, updown: "ud.UpdownStatus | None" = None) -> str:
    """知らない言葉の聞き返し。上下の向きが確認済みのときだけ「下に3ミリ」を例に出す（渡さなければ未確認として扱う）。"""
    quoted = "".join(f"「{w}」" for w in words)
    if op == "speed":
        return f"{quoted}が分かりませんでした。速度の向きと量を、もう一度言ってください（例：速度を10%下げて）"
    if _updown(updown).confirmed:
        return (f"{quoted}が分かりませんでした。軸（X・Y・Z）と向きと量を、もう一度言ってください"
                "（例：Y座標を3ミリ下げて／下に3ミリ）")
    return (f"{quoted}が分かりませんでした。軸（X・Y・Z）と向きと量を、もう一度言ってください"
            "（例：Zマイナス3ミリ／Yのプラス3ミリ）")


_UPDOWN_WORD = re.compile(r"上げ|下げ|上に|下に|上へ|下へ|上方向|下方向")
_FOLLOWUP_HEAD = {ud.MISMATCH: "このジョブの座標系・ツール番号・ロボットが、上下の向きを確認したときと違うため",
                  ud.CHANGED: "上下の向きの設定が、確認したあとで変わっているため"}


def updown_ask_message(facts: UtteranceFacts, updown: "ud.UpdownStatus | None") -> str:
    """上下の向きが未確認のときの、会話の聞き返し（唐川さんの決定 11/5）。意図を受け止めてから、分からないことだけを聞く。

    - 数（mm）と上下の言葉（上に・上げ・下へ など）は、プログラムが発話から読んで入れる。Claude には作らせない
    - Zプラス・Zマイナスのどちらかは決めない（例は両方を出す）。候補は作らない（pre_check の聞き返しのまま）
    - 状態の表示（ud.MSG_UNCONFIRMED。state.updown・候補の表示）は変えない。不一致・変更済みは今の状態ごとの文
    - 上と下の両方がある・量に符号が付いているなど、決まった形にならないときは、意図の言葉なしの決まった文（UPDOWN_UNSET_PLAIN）
    - 「3Dの矢印を見ながら確認できます」は出さない（画面は 3D と 2D を切り替えられ、サーバーからは 3D が出ているか確かめられない）
    - 「何mm」とは聞かない（この聞き返しのあとの「3ミリ」だけの答えは、前の発話に足さない。CHECKER_026 再修正 P0-1）
    """
    st = _updown(updown)
    if st.status != ud.UNCONFIRMED:
        return st.message
    if "%" in facts.text or _SPEED.search(facts.text) or any(a.unit == "%" for a in facts.amounts):
        return st.message  # 速度の話（「10%下げて」）を、位置の上下として受け止めない（QA 002 N3・Safety 002 Low-2）
    words = list(_UP.finditer(facts.text)) + list(_DOWN.finditer(facts.text))
    ups, downs = bool(_UP.search(facts.text)), bool(_DOWN.search(facts.text))
    if ups == downs or not words:
        return UPDOWN_UNSET_PLAIN
    m = min(words, key=lambda x: x.start())
    w = "上" if ups else "下"
    said = m.group(0)
    phrase = {"上げ": "上げたい", "下げ": "下げたい"}.get(said)
    if phrase is None:
        phrase = f"{said}{'に' if said.endswith('方向') else ''}動かしたい"
    a = facts.amount
    head = f"この設備では、まだ「{w}」の方向が決まっていません。"
    if a is not None and a.unit == "mm" and a.sign is None and not facts.unclear_amounts:
        v = _num(a.value)
        return (f"{v}mm{phrase}んですね。{head}ZプラスとZマイナスのどちらになりますか？ "
                f"「Zプラス{v}ミリ」か「Zマイナス{v}ミリ」のように、向きと量を一緒に言ってください")
    if a is not None and a.sign is not None:
        return UPDOWN_UNSET_PLAIN
    return (f"{phrase}んですね。{head}ZプラスとZマイナスのどちらに、どれだけ動かしますか？ "
            "「Zプラス3ミリ」か「Zマイナス3ミリ」のように、向きと量を一緒に言ってください")


UPDOWN_UNSET_MARK = "の方向が決まっていません。"
UPDOWN_UNSET_PLAIN = ("この設備では、まだ「上」「下」の方向が決まっていません。ZプラスとZマイナスのどちらになりますか？ "
                      "「Zプラス3ミリ」か「Zマイナス3ミリ」のように、向きと量を一緒に言ってください")


def is_updown_unset_ask(text: object) -> bool:
    """上下の向きが未確認のときの、会話の聞き返しの文か（updown_ask_message が作る形）。テスト・表示の見分けに使う。"""
    return (isinstance(text, str) and "この設備では、まだ「" in text and UPDOWN_UNSET_MARK in text
            and "ZプラスとZマイナスのどちら" in text and "「Zプラス" in text and "「Zマイナス" in text)


def updown_word(facts: UtteranceFacts) -> str:
    """発話の中の上下の言葉（「下げて」「上に」）。示すためだけ。見つからなければ「上」「下」。"""
    m = _UPDOWN_WORD.search(facts.text)
    if m is None:
        return "上」「下"
    return {"上げ": "上げて", "下げ": "下げて"}.get(m.group(0), m.group(0))


def updown_followup_message(updown: "ud.UpdownStatus | None", word: str) -> str:
    """上下の案内で聞き返したすぐあとの答え（「3ミリ」）への案内。前の発話とはつながない。状態ごとに文を変える。"""
    st = _updown(updown)
    head = _FOLLOWUP_HEAD.get(st.status, "上下の向きが確認されていないため")
    return (f"{head}、「{word}」は使えません。「Zプラス3ミリ」「Zマイナス3ミリ」のように、"
            "軸と向きと量を一緒に言ってください")


QUESTION_RESTATE_MESSAGE = "質問の形の指示では変更しません。してほしいことを、もう一度言ってください"

# 言い終わりの「〜します」の確かめ（CHECKER_026 M3。唐川さんの判断 2026-11-03 13:27）
SHIMASU_ASK = "{what}変更をご希望ですか？ それとも操作の説明でしょうか？"
SHIMASU_FIXED_WHAT = "この"
WANT_CHANGE_LABEL = "変更したい"
EXPLAIN_LABEL = "説明です"
# ［説明です］の選択肢が送る文。この文（だけ）の指示は、何も変えずに EXPLAIN_REPLY を返す（Session.hear・WebFlow.hear）。
# 位置番号を足さない。承認・候補・会話の状態も変えない
EXPLAIN_CHOICE_TEXT = EXPLAIN_LABEL
EXPLAIN_REPLY = "分かりました。変更はしません"
_EXPLAIN_STRIP = re.compile(r"[\s　。．.!！、,]+")


def is_explain_choice(text: object) -> bool:
    """［説明です］の選択肢の文（「説明です」だけ。前後の空白・句読点は無視）か。"""
    return isinstance(text, str) and _EXPLAIN_STRIP.sub("", unicodedata.normalize("NFKC", text)) == EXPLAIN_CHOICE_TEXT


def is_shimasu_confirmation(v: Verdict) -> bool:
    """「〜します」の2択の聞き返しか。"""
    return v.status == "ask" and len(v.choices) == 2 and v.choices[1] == (EXPLAIN_LABEL, EXPLAIN_CHOICE_TEXT)


def _shimasu_what(facts: UtteranceFacts) -> str:
    """「3mm下げる」「C00004を3mm上げる」「Yのマイナス方向に3mm動かす」「速度を10%下げる」。決まらなければ「この」（固定の言い方）。

    数字・軸・向きはプログラムが読んだもの（facts）から入れる。上下を軸に当てはめた言い方はしない（言った言葉の上げ・下げのまま）。
    """
    a = facts.amount
    value = facts.requested_value()
    if a is None or value is None or value == 0:
        return SHIMASU_FIXED_WHAT
    w, v = _where(facts), _num(a.value)
    if facts.op == "speed" and a.unit == "%":
        return f"{w}速度を{v}%{'上げる' if value > 0 else '下げる'}"
    if facts.op != "move" or a.unit != "mm" or len(facts.axes) != 1:
        return SHIMASU_FIXED_WHAT
    if facts.vertical:
        return f"{w}{v}mm{'上げる' if value > 0 else '下げる'}"
    return f"{w}{facts.axis}の{'プラス' if value > 0 else 'マイナス'}方向に{v}mm動かす"


def shimasu_request_text(facts: UtteranceFacts, selected: str | None = None) -> str | None:
    """［変更したい］が送る指示の文。プログラムが読んだ facts から作る（言葉を足さない・推測しない）。作れなければ None。

    - 位置：言った位置番号、無ければ選んでいる位置（あとで別の位置を選んでも、その位置に当てない）。どちらも無ければ付けない
    - 上・下から読んだ Z は「3ミリ下げて」のまま（次の指示のときに、上下の向きの照合をもう一度通す。Z の文字に書き換えない）
    - 軸の文字で言ったものは「Yのマイナス3ミリ」、速度は「C00005の速度を10%下げて」
    """
    a = facts.amount
    value = facts.requested_value()
    if a is None or value is None or value == 0 or facts.place_ref or facts.unknown_words or facts.question:
        return None
    pos = facts.position or selected
    v = _num(a.value)
    if facts.op == "speed" and a.unit == "%":
        return f"{pos + 'の' if pos else ''}速度を{v}%{'上げて' if value > 0 else '下げて'}"
    if facts.op != "move" or a.unit != "mm" or len(facts.axes) != 1:
        return None
    head = f"{pos}を" if pos else ""
    if facts.vertical:
        return f"{head}{v}ミリ{'上げて' if value > 0 else '下げて'}"
    return f"{head}{facts.axis}の{'プラス' if value > 0 else 'マイナス'}{v}ミリ"


SHIMASU_RESTATE_MESSAGE = "「〜ます」の言い方では変更しません。してほしいことを、もう一度言ってください"


SHIMASU_CHOOSE_SIGN_MESSAGE = ("プラスかマイナスかを、ボタンで選んでください"
                               "（声で言うときは「Zマイナス3ミリ」のように、量と向きを一緒に言ってください）")


def shimasu_restate_message(facts: UtteranceFacts, updown: "ud.UpdownStatus | None" = None) -> str:
    """「〜します」で量か方向が足りないときの、言い直してもらう文（答えを待つ形にしない。QA P1-A）。
    上下の向きが確認済みのときだけ、上下の言葉の例も出す。"""
    if facts.op == "speed":
        return "速度の向きと量を一緒に、もう一度言ってください（例：速度を10%下げて）"
    if _updown(updown).confirmed:
        return "量と向きを一緒に、もう一度言ってください（例：Zマイナス3ミリ／下に3ミリ）"
    return "量と向きを一緒に、もう一度言ってください（例：Zマイナス3ミリ）"


def _shimasu_check(facts: UtteranceFacts, v: Verdict, selected: str | None = None,
                   updown: "ud.UpdownStatus | None" = None) -> Verdict:
    """言い終わりの「〜します」：候補にしない。外した文がふつうの指示として通るとき（v が ok）だけ、
    変更の希望か操作の説明かを2択で確かめる（CHECKER_026 M3、QA P1-1）。

    外した文が止まる・聞き返すなら、その答え（具体的な理由）をそのまま返す（2択で上書きしない）。
    上限・0mm・位置がジョブにあるかは Session.check_shimasu が続けて調べる（ここではジョブを知らない）。
    ［変更したい］は facts から作った指示の文（shimasu_request_text）を、ふつうの指示として送る
    （確認・検査・上限・確認表示・承認ボタンを全部通る）。
    """
    if v.status == "ask" and v.needs_answer:
        # 量・方向・向きが足りない：「〜します」の発話には答えを足さないので、答えを待つ形で聞かない。
        # 選択肢（［Zのプラス 3mm］［Zのマイナス 3mm］）があれば、ボタンで選ぶよう言う。無ければ一緒に言い直してもらう
        if v.choices:
            return _ask(SHIMASU_CHOOSE_SIGN_MESSAGE, v.choices)
        return _ask(shimasu_restate_message(facts, updown))
    if v.status != "ok":
        return v
    if facts.unknown_words:
        return _ask(f"{''.join(f'「{w}」' for w in facts.unknown_words)}が分かりませんでした。もう一度指示してください")
    if facts.requested_value() == 0:
        return _stop("移動量が0のため、変更しません" if facts.op == "move" else "変更量が0のため、変更しません")
    t = shimasu_request_text(facts, selected)
    if t is None:
        return _ask(SHIMASU_RESTATE_MESSAGE)
    return _ask(SHIMASU_ASK.format(what=_shimasu_what(facts)),
                ((WANT_CHANGE_LABEL, t), (EXPLAIN_LABEL, EXPLAIN_CHOICE_TEXT)))


def pre_check(facts: UtteranceFacts, selected: str | None = None,
              updown: "ud.UpdownStatus | None" = None) -> Verdict:
    """Claude に頼らず、発話だけで止める・聞き返すことが決まるかを調べる。selected：画面で選んでいる位置（文の案内だけに使う）。
    updown：上下の向きの照合の結果（渡さなければ未確認として扱う）。"""
    v = _pre_check(facts, selected, updown)
    if facts.shimasu_text is not None and not facts.question:
        return _shimasu_check(facts, v, selected, updown)
    if v.status != "ok" or not facts.question:
        return v
    # 疑問の形（「3mm下げて？」）：候補にせず、？を外した同じ指示でよいかを確かめる。選択肢はその文の1つだけ。
    # 知らない言葉・打ち消しがあれば選択肢を出さない（打ち消しは _pre_check で止まっている）
    if facts.unknown_words:
        return _ask(f"{''.join(f'「{w}」' for w in facts.unknown_words)}が分かりませんでした。もう一度指示してください")
    t = facts.question_text
    if not t:
        return _ask(QUESTION_RESTATE_MESSAGE)
    return _ask(f"「{t}」でよいですか？", ((t, t),))


def _place_rewrite(facts: UtteranceFacts, position: str) -> str | None:
    """「C00006の置くところを下に3mm」→「C00006を下に3mm」。場所の言葉を位置番号に置き換えた文。作れなければ None。"""
    raw = facts.question_text or facts.shimasu_text or facts.raw
    i = raw.find(facts.place_ref) if facts.place_ref else -1
    if i < 0:
        return None
    after = re.sub(r"^\s*(?:を|は|の)?\s*", "", raw[i + len(facts.place_ref):])
    text = f"{position}を{after}"
    f = read_utterance(text)
    if f.unknown_words or f.negations or f.undo_negations or f.place_ref or f.question or f.op != facts.op:
        return None
    return text


def _place_check(facts: UtteranceFacts, selected: str | None) -> Verdict:
    """場所の言葉（「置くところ」）：どの位置かは、プログラムも Claude も決めない。"""
    p = facts.place_ref
    if facts.place_as_destination:
        return _ask(f"「{p}」へ動かす指示は扱いません。位置を選んで、軸と向きと量で言ってください")
    if facts.positions:
        pos = facts.positions[0]
        t = _place_rewrite(facts, pos)
        msg = f"「{p}」が {pos} かどうかは確かめていません。{pos} でよいですか？"
        return _ask(msg, ((t, t),) if t else ())
    if selected:
        t = _place_rewrite(facts, selected)
        hint = f"「{t}」と言うか" if t else "位置番号を付けて言うか"
        return _ask(f"「{p}」がどの位置か決まっていません。選んでいる {selected} のことなら{hint}、位置を選び直してください")
    return _ask(f"「{p}」がどの位置か決まっていません。位置を選んでください")


def _pre_check(facts: UtteranceFacts, selected: str | None = None,
               updown: "ud.UpdownStatus | None" = None) -> Verdict:
    return _pre_check_core(facts, selected, updown)


def _pre_check_core(facts: UtteranceFacts, selected: str | None = None,
                    updown: "ud.UpdownStatus | None" = None) -> Verdict:
    st = _updown(updown)
    found = facts.negations + facts.undo_negations
    if found:
        # 打ち消しの言葉を捨てて、言ったことと逆の操作にしない。選択・比較・元に戻すも含めて、いちばん先に止める（選択肢も出さない）。
        # 「やめて」「元に戻して」「取り消して」「キャンセル」だけの指示は、元に戻す指示として使う（undo_negations が空）
        return _stop(NEGATION_MESSAGE.format(word="".join(f"「{w}」" for w in found)))
    if facts.repeat is not None:
        # 繰り返し・往復：候補にしない。「2回」「行き来」はどちらの意味かを、プログラムが作る2つの選択肢で聞く。
        # どちらを選んでも、最後は止める（動きの追加・繰り返しはしない）
        n, kind = facts.repeat
        if kind != "ambiguous":
            return _stop(REPEAT_STOP_MESSAGE)
        if n is None:
            return _ask("繰り返しの移動ですか、往復ですか？", (("繰り返しの移動", "繰り返しの移動"), ("往復", "往復")))
        return _ask(f"{n}回の移動ですか、{n}往復ですか？", ((f"{n}回の移動", f"{n}回の移動"), (f"{n}往復", f"{n}往復")))
    if facts.stop_reasons:
        return _stop("。".join(facts.stop_reasons))
    if not facts.kinds:
        if facts.unknown_words:
            return _ask(f"{''.join(f'「{w}」' for w in facts.unknown_words)}が分かりませんでした。何をするか、もう一度指示してください")
        return _ask("何をするか分かりませんでした。もう一度指示してください")
    ops = [k for k in facts.kinds if k in OPS]
    if len(ops) > 1:
        return _ask("1回の指示では、1つのことだけを行います。1つずつ指示してください")
    if facts.bad_positions:
        return _ask(f"位置番号 {'・'.join(facts.bad_positions)} の形が分かりません。C と5桁の数字で言ってください")
    if len(facts.positions) > 1:
        return _ask(f"位置が {len(facts.positions)}つ（{'・'.join(facts.positions)}）あります。"
                    "1回の指示で変えるのは1つの位置だけです。1つずつ指示してください")
    op = facts.op
    if op in ("move", "speed") and facts.unknown_words:
        # 知らない言葉を捨てて、残りの言葉だけで候補にしない。選択肢も出さない（どれを出しても推測か、言葉を捨てることになる）
        return _ask(unknown_words_message(facts.unknown_words, op, st))
    if op in ("move", "speed") and facts.place_ref and not facts.sides:  # 右・左は下の SIDE_MESSAGE
        # 場所の言葉は、量・向きを聞く前に調べる（「置くところへ動かして」に「何mm？」と聞いて、続きの答えで別の位置の候補にしない）
        return _place_check(facts, selected)
    if op in ("move", "speed") and facts.stray_signs > 0:
        return _ask("プラス・マイナスがどこに付くか分かりません。「Xのマイナス5ミリ」のように、軸のすぐ後に言ってください")
    if op in ("move", "speed") and facts.unclear_amounts and _number_count(facts) > 1:
        # 「Z+3、Y+4」「Y-の方向に10と20」「Z+3と5mm」：単位より先に、1回に1つだけのことを聞く（QA 001 L1）。
        # 単位だけを聞くと、付けて言い直したあとに「量が2つ」で断ることになる
        return _ask("1回の指示で動かすのは、1つの方向に1つの量だけです。1つずつ、軸と向きと量（単位も）を言ってください")
    if op in ("move", "speed") and facts.unclear_signs and (
            facts.axis_signs or facts.speed_signs or len(facts.unclear_signs) > 1
            or any(a.sign is not None for a in facts.amounts)):
        # 「Yプラスの方向にマイナス3」：単位の無い数にも向きが付いていて、向きの指定が2つ。mm まで言ったときと同じく、
        # どちらも選ばない。止めて言い直してもらう（答えとして次の発話に足さない。足すと片方の符号が消える。Safety High-1）
        return _stop(TWO_SIGNS_STOP)
    if op in ("move", "speed") and facts.earlier_signs:
        v = _earlier_sign_check(facts)
        if v is not OK:
            return v
    if op == "move":
        if len(facts.amounts) > 1 or len(facts.axes) > 1:
            moves = _split_moves(facts.raw)
            if moves:  # 2つ以上の変更：各軸の量を、プログラムが読んだとおりに示して、1つずつ指示してもらう
                listed = "、".join(f"{a}方向 {_fmt(v)}mm" for a, v in moves)
                return _ask(f"変更が{len(moves)}つ入っています（{listed}）。"
                            "1回に動かすのは1つの方向だけです。1つずつ指示してください")
        if facts.sides:
            return _ask(SIDE_MESSAGE_UPDOWN if st.confirmed else SIDE_MESSAGE)
        # 「Zを3mm下げて」（Z の文字＋上げ・下げ。「座標」「値」が無い）：Z の値の増減か、上下の指示か決まらない。
        # 上下の向きが確認済みでも聞き返す（唐川さんの決定 2026-11-03。D1 と同じ形）
        bare_z = is_bare_z_updown(facts)
        if facts.vertical and (len(facts.axes) > 1 or bare_z):
            others = [a for a in facts.axes if a != "Z"]
            other = "・".join(others) if others else "Z"
            choices: tuple[tuple[str, str], ...] = ()
            a = facts.amount
            shape = (len(facts.axes) == 2 and bool(others)) or (bare_z and len(facts.axes) == 1)
            if (shape and len(facts.axis_signs) == 1 and a is not None and a.sign is None
                    and a.unit == "mm" and not facts.unclear_amounts):
                sign, v, w = facts.axis_signs[0], _num(a.value), _where(facts)
                up = sign > 0
                wn = f"{facts.position}の" if facts.position else ""  # 「C00005のY座標を…」（「を」を続けない。QA P2-6）
                choices = ((f"{other}座標を {'+' if up else '−'}{v}mm", f"{wn}{other}座標を{v}mm{'上げて' if up else '下げて'}"),)
                if st.confirmed:
                    choices += ((f"{'上' if up else '下'}方向へ {v}mm", f"{w}{'上' if up else '下'}に{v}mm"),)
            if not st.confirmed:
                return _ask(f"{other} の値を変えるなら「{other}座標を…上げて／下げて」と言ってください。" + st.message, choices)
            if bare_z:
                return _ask("Z の値を変える指示と、上げ・下げ（上下）の指示の2つの読み方があります。"
                            "Z の値なら「Z座標を…上げて／下げて」、上下なら「上に…／下に…」のように言ってください", choices)
            return _ask(f"{other} と、上げ・下げ（上下＝Z）の2つの読み方があります。"
                        f"{other} の値なら「{other}座標を…上げて／下げて」、上下なら「上に…／下に…」のように言ってください",
                        choices)
        if facts.vertical and not st.confirmed:
            # 上下の向きが確認できていない：上・下を Z にしない。状態ごとの案内（未確認・不一致・変更済み）。選択肢は出さない
            return _ask(updown_ask_message(facts, st))
        v = _amount_check(facts, "mm", "何mm動かしますか？")
        if v is not OK:
            return v
        if not facts.axes:
            return _ask_slot(AXIS_ASK_UPDOWN if st.confirmed else AXIS_ASK)
        if len(facts.axes) > 1:
            return _ask("方向が2つ以上あります。1回の指示で動かすのは1つの方向だけです")
        if facts.sign_count() == 0:
            a, ax, w = facts.amount, facts.axis, _where(facts)
            choices = ()
            if a is not None and a.unit == "mm":
                v = _num(a.value)
                choices = ((f"{ax}のプラス {v}mm", f"{w}{ax}プラスに{v}mm"), (f"{ax}のマイナス {v}mm", f"{w}{ax}マイナスに{v}mm"))
            said = ""
            if len(set(facts.earlier_signs)) == 1:  # 前に言った向きを示す（それを今の向きとしては使わない。Safety 002 Medium-1）
                said = f"最初は「{_SIGN_WORD[facts.earlier_signs[0]]}」と聞こえました"
            tail = f"（{said}。「{ax}のマイナス」のように向きも言ってください）" if said else f"（「{ax}のマイナス」のように向きも言ってください）"
            return _ask_slot(f"{ax}方向のプラスですか、マイナスですか？{tail}", choices)
        if facts.requested_value() is None:
            return _ask("向きの指定が2つあります。どちらの向きか、もう一度指示してください")
    elif op == "speed":
        v = _amount_check(facts, "%", "今の速度に対して、何%変えますか？")
        if v is not OK:
            return v
        if facts.requested_value() is None:
            return _ask_slot("速度を上げますか、下げますか？（向きが分からないか、2つあります）")
    elif "select" in facts.kinds and not facts.positions and not facts.refers_to_selection:
        return _ask("どの位置ですか？ 位置番号（C と5桁の数字）で言ってください")
    return OK


def _number_count(facts: UtteranceFacts) -> int:
    """発話の中の、算用数字の量の数（単位のある量と、単位の無い・違う数）。漢数字・「少し」・区切りのある数字などは数えない。"""
    return len(facts.amounts) + sum(1 for u in facts.unclear_amounts if re.match(r"「[0-9]", u) and u.endswith(NOT_UNIT_NOTE))


TWO_SIGNS_STOP = ("向きの指定が2つあります（方向と、数の前のプラス・マイナス）。どちらの向きも選ばずに止めました。"
                  "軸と向きと量を一緒に、もう一度言ってください")  # 例は出さない（片方の向きを勧めない）
_SIGN_WORD = {1: "プラス", -1: "マイナス"}


EARLIER_TWO_SIGNS_STOP = ("前の指示に、向きの言葉が2つ（プラスとマイナス、または上と下）ありました。どちらの向きも選ばずに止めました。"
                          "軸と向きと量を一緒に、もう一度言ってください")


def _earlier_sign_check(facts: UtteranceFacts) -> Verdict:
    """前の発話で言った向きの言葉（earlier_signs）と、今の向きの言葉が食い違わないか（QA 002 N1・Safety 002 Medium-1）。

    - 前の言葉どうしが食い違う（「Yプラス方向にマイナス」）：答えと合わせずに止める
    - 今の言葉（答えの発話の言葉・読んだ向き）のどれか1つでも前と食い違う：候補にせず、食い違いを示して止める
    どちらも _stop（次の発話を答えとして足さない。もう一度はっきり言えば、ふつうの指示として読む）。
    前の向きを、今の向きとして使うことはしない（推測しない）。文の符号・数はプログラムが読んだもの。
    """
    before = list(dict.fromkeys(facts.earlier_signs))
    if not before:
        return OK
    if len(before) > 1:
        return _stop(EARLIER_TWO_SIGNS_STOP)
    first = before[0]
    now = (list(facts.said_signs) + [a.sign for a in facts.amounts if a.sign is not None]
           + list(facts.axis_signs) + list(facts.speed_signs) + list(facts.unclear_signs))
    other = next((x for x in now if x != first), None)
    if other is None:
        return OK
    a = facts.amount
    head = f"最初は{_SIGN_WORD[first]}、今は{_SIGN_WORD[other]}と聞こえました。どちらですか？ "
    if facts.op == "speed":
        ex = f"（例：速度を{_num(a.value)}%上げて／速度を{_num(a.value)}%下げて）" if a is not None and a.unit == "%" else ""
        return _stop(head + f"速度を上げるか下げるかと量を一緒に、もう一度言ってください{ex}")
    ex = ""
    if facts.axis and a is not None and a.unit == "mm":
        v = _num(a.value)
        ex = f"（例：{facts.axis}プラス{v}ミリ／{facts.axis}マイナス{v}ミリ）"
    return _stop(head + f"軸と向きと量を一緒に、もう一度言ってください{ex}")


_SPLIT_MOVES = re.compile(r"[、,，。]|そして|それから")


def _split_moves(raw: str) -> list[tuple[str, float]] | None:
    """「上に2mm、Yプラスに3mm」を、区切りごとの（軸, 符号つきの量）に分ける。1つでも読めなければ None。

    1つの変更にまとめることはしない（各軸の量を示して、1つずつ指示してもらうためだけに使う）。
    """
    parts = [p for p in _SPLIT_MOVES.split(raw) if p.strip()]
    if len(parts) < 2:
        return None
    out = []
    for part in parts:
        f = read_utterance(part)
        if (f.op != "move" or f.sides or f.unclear_amounts or len(f.axes) != 1 or len(f.amounts) != 1
                or f.amounts[0].unit != "mm" or f.positions):
            return None
        v = f.requested_value()
        if v is None:
            return None
        out.append((f.axis, v))
    return out


def _amount_check(facts: UtteranceFacts, unit: str, missing_question: str) -> Verdict:
    if facts.unclear_amounts and all(_NO_UNIT_ITEM.fullmatch(u) for u in facts.unclear_amounts):
        # 単位が無いだけ（「Z+3」）：足りない単位をやさしく聞く。単位を補った候補は作らない（数字はプログラムが読んだまま）
        found = [_NO_UNIT_ITEM.fullmatch(u).group(1) for u in facts.unclear_amounts]
        nums = "".join(f"「{n}」" for n in found)
        # 例は符号・上げ下げを含まない形（言った向きと逆の例を出さない。Safety Medium-1）。数は言ったまま
        if unit == "mm":
            return _ask(f"量がはっきりしません。{nums}に単位が付いていません。"
                        f"「{found[0]}ミリ」のように、単位も付けて、もう一度言ってもらえますか？")
        return _ask(f"量がはっきりしません。{nums}に単位が付いていません。"
                    f"「{found[0]}%」のように、単位も付けて、もう一度言ってもらえますか？")
    if facts.unclear_amounts:
        return _ask(f"量がはっきりしません（{'・'.join(facts.unclear_amounts)}）。"
                    f"算用数字と {'mm（ミリ）' if unit == 'mm' else '%'} で量を言ってください")
    if not facts.amounts:
        return _ask_slot(missing_question)  # 量が無い：答えを待つ形の質問（ほかは具体的な理由）
    if len(facts.amounts) > 1:
        return _ask("量が2つ以上あります。1つの量で指示してください")
    if facts.amounts[0].unit != unit:
        word = "移動" if unit == "mm" else "速度"
        return _ask(f"{word}の量は {'mm（ミリ）' if unit == 'mm' else '%'} で言ってください")
    return OK


# ---- Claude（またはモック）の提案 ----
_ALLOWED_KEYS = {"kind", "position", "axis", "side", "delta_mm", "percent"}


def _real(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass(frozen=True)
class ProposedChange:
    """道具 propose_change の引数。ジョブの文字列や説明文は持たない。"""
    kind: str                      # "shift" または "speed"
    position: str
    axis: str | None = None        # "X" / "Y" / "Z"
    side: str | None = None        # "右" / "左"（軸は枠で決める）
    delta_mm: float | None = None  # 符号つき
    percent: float | None = None   # 今の速度に対する割合（符号つき）

    @classmethod
    def from_tool_args(cls, args: Mapping) -> "ProposedChange":
        if not isinstance(args, Mapping):
            raise ValueError("提案の形が読めません")
        extra = set(args) - _ALLOWED_KEYS
        if extra:
            raise ValueError(f"提案に使えない項目（{'・'.join(sorted(map(str, extra)))}）が入っています")
        kind = args.get("kind")
        position = args.get("position")
        if kind not in ("shift", "speed"):
            raise ValueError("提案の種類は、位置の移動か速度の変更だけです")
        if not isinstance(position, str) or normalize_position(position) is None:
            raise ValueError("提案の位置番号が読めません")
        axis, side = args.get("axis"), args.get("side")
        delta, percent = args.get("delta_mm"), args.get("percent")
        if kind == "shift":
            if (axis is None) == (side is None):
                raise ValueError("位置の移動の提案には、軸か左右のどちらか1つが要ります")
            if axis is not None and axis not in ("X", "Y", "Z"):
                raise ValueError("提案の軸が読めません")
            if side is not None and side not in ("右", "左"):
                raise ValueError("提案の左右が読めません")
            if not _real(delta) or percent is not None:
                raise ValueError("提案の移動量が数値ではありません")
        else:
            if not _real(percent) or delta is not None or axis is not None or side is not None:
                raise ValueError("提案の速度の割合が数値ではありません")
        return cls(kind=kind, position=normalize_position(position), axis=axis, side=side,
                   delta_mm=float(delta) if delta is not None else None,
                   percent=float(percent) if percent is not None else None)


def _fmt(x: float) -> str:
    return f"{x:+g}"


def is_bare_z_updown(facts: UtteranceFacts) -> bool:
    """「Zを3mm下げて」の形：Z を軸の文字で言い、上げ・下げ（上下の言葉）もあり、「Z座標」「Zの値」ではない。"""
    return facts.vertical and "Z" in facts.explicit_axes and "Z" not in facts.value_axes


def axis_source_gate(facts: UtteranceFacts, proposal: "ProposedChange",
                     updown: "ud.UpdownStatus | None" = None) -> Verdict:
    """最後の関門（CHECKER_028 K1）：候補を作る直前に、提案の軸の「出どころ」を発話の事実で確かめる。

    Session.propose → check_proposal の始めの1か所だけで呼ぶ（会話・ボタン・合わせた答え・Claude の答え・M3 の［変更したい］・
    疑問の選択肢・D1 の選択肢のどれも、この経路を通る。tests/test_c028_p0.py で固定する）。
    - 発話に無い軸の提案は通さない
    - 上下の言葉から来た Z（vertical）は、上下の向きが確認済みのときだけ。「Zを3mm下げて」（座標・値の無い Z と上下の言葉）は通さない
    - 上下の言葉からでない Z は、軸の文字（explicit_axes。「Zマイナス」「Z座標」「Zの値」）で言ったときだけ
    """
    p = proposal
    if not isinstance(p, ProposedChange) or p.kind != "shift" or p.axis is None:
        return OK
    st = _updown(updown)
    if p.axis not in facts.axes:
        return _ask(f"指示の中に {p.axis} の軸の言葉がありません。軸と向きと量を、もう一度指示してください")
    vertical_z = facts.vertical and p.axis == "Z"
    if vertical_z and is_bare_z_updown(facts):
        return _ask("Z の値を変える指示か、上げ・下げ（上下）の指示か決まりません。"
                    "Z の値なら「Z座標を…上げて／下げて」と言ってください")
    if vertical_z and not st.confirmed:
        return _ask(st.message)
    if p.axis not in facts.explicit_axes and not vertical_z:
        if p.axis == "Z" and not st.confirmed:
            return _ask(st.message)
        return _ask(f"指示の中に {p.axis} の軸の言葉がありません。軸と向きと量を、もう一度指示してください")
    return OK


def check_proposal(facts: UtteranceFacts, proposals: "ProposedChange | Sequence[ProposedChange]",
                   selected: str | None = None, updown: "ud.UpdownStatus | None" = None) -> Verdict:
    """提案を、発話から取り出した事実と照らし合わせる（受入テスト5・7・11・16）。
    上下の向きが確認済みでなければ、上・下から Z を作った発話（facts.vertical）の提案は通さない。
    最後の関門 axis_source_gate で、どの提案も軸の出どころを必ず確かめる（CHECKER_028 K1）。"""
    plist = [proposals] if isinstance(proposals, ProposedChange) else list(proposals)
    # 最後の関門（ここ1か所）。どの提案も必ず通す。断る文は、もっと具体的な理由（打ち消し・知らない言葉・種類や位置や量の
    # 食い違いなど）があればそちらを先に返し、無ければ関門の理由を返す（どちらでも候補は作らない。OK を返すのは関門のあとだけ）
    gates = [axis_source_gate(facts, one, updown) for one in plist]
    v = pre_check(facts, selected, updown)
    if v.status != "ok":
        return v
    if len(plist) != 1:
        return _ask("1回の提案で変えるのは1つの位置、または1つの命令だけです。1つずつ指示してください")
    p = plist[0]
    if p.side is not None:
        return _stop(SIDE_MESSAGE)
    op = facts.op
    if op not in ("move", "speed"):
        return _stop("この指示は変更を求めていないため、変更の提案は使いません")
    if (op == "move") != (p.kind == "shift"):
        said = "位置の移動" if op == "move" else "速度の変更"
        return _ask(f"指示は{said}ですが、提案の種類が違います。もう一度指示してください")
    expected = facts.position or selected
    if expected is None:
        return _ask("どの位置ですか？ 位置を選ぶか、位置番号で言ってください")
    if p.position != expected:
        return _ask(f"指示の位置（{expected}）と提案の位置（{p.position}）が違います。もう一度指示してください")
    said = facts.requested_value()
    if p.kind == "shift":
        if p.axis != facts.axis:
            return _ask(f"指示の方向（{facts.axis}）と提案の方向（{p.axis}）が違います。もう一度指示してください")
        if not math.isclose(p.delta_mm, said, abs_tol=1e-9):
            return _ask(f"指示の量（{_fmt(said)}mm）と提案の量（{_fmt(p.delta_mm)}mm）が違います。もう一度、量を指示してください")
    else:
        if not math.isclose(p.percent, said, abs_tol=1e-9):
            return _ask(f"指示の量（{_fmt(said)}%）と提案の量（{_fmt(p.percent)}%）が違います。もう一度、量を指示してください")
    for gate in gates:  # 最後の関門の結果。ここまでの照らし合わせに通っても、関門に断られたら通さない
        if gate.status != "ok":
            return gate
    return OK


# ---- Claude の文：数字は空欄にプログラムが入れる（受入テスト14） ----
_BLANK = re.compile(r"\{([^{}]+)\}")
_DIGIT = re.compile(r"[0-9０-９〇零一二三四五六七八九十百千万]")


def fill_blanks(template: str, values: Mapping[str, str], fallback: str) -> str:
    """Claude が書いた空欄つきの文に、プログラムが値を入れる。

    空欄以外に数字がある・断定の言葉がある・知らない空欄がある・空欄が無い値がある 文は使わず、
    固定文（fallback。こちらも空欄に値を入れる）にする。
    """
    def fill(t: str) -> str:
        return _BLANK.sub(lambda m: str(values[m.group(1)]), t)

    names = _BLANK.findall(template)
    outside = _BLANK.sub("", template)
    usable = (
        all(n in values for n in names)
        and not _DIGIT.search(normalize(outside))
        and not contains_forbidden(template)
        and "{" not in outside and "}" not in outside
    )
    return fill(template) if usable else fill(fallback)
