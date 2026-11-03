"""Claude との会話を「ロボットと話している」ようにするための、プログラムの側の決まり（CHECKER_031 E1〜E7）。

このモジュールは Claude を呼ばない。Session（候補・承認・選択・作業ログ）にも触らない。受け取るのはジョブ（jbi.Job）と、
人の文と、Claude の答えのうちプログラムが確かめた部分（根拠の行番号など）だけ。

- E1 やさしい言葉：最初の説明に位置番号（C00001）・命令の名前（MOVL・MOVJ）・「ジョブ」「JOB」があれば、Claude に
  作り直しを頼まず、プログラムが事実から作る決まった文にする。質問の「？」が2つ以上の返事も使わない。
  位置番号・行番号・命令は、根拠の行からプログラムが作る「補足」として別に出す（supplement）。
- E2 人が教えた意味：登録するかはプログラムが人の文と事実で決める（Claude の判定には頼らない。Claude の言い換えは登録しない）。
  相づちだけ・ためらい・打ち消し・対象を1つに決められない・事実と合わない、では登録しない。
  「ボタンじゃなくてスイッチ」は前の意味の訂正（置き換え）。意味はこの画面のメモリの中だけ（ファイル・records/ に書かない）。
  データ（候補・承認・ジョブ）が変わったら、意味と対象の対応を確かめ直し、確かめられなければ無効にして知らせる。
  意味は、変更してよい範囲・上限・止める理由・承認には使わない（/api/hear の流れはこのモジュールを読まない）。
- E4 光らせる範囲：根拠の行番号からプログラムが決める（行がくり返しの区間に入っていれば、その区間の行の全部）。
- E5 回数：プログラムが人の文から読む。Claude の change_request の数と違えば「食い違い」として記録する（候補は作らない）。
  区間が2つ以上で決められなければ、対象を決めない。打ち消し・ためらいがあれば読まない。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import dialog as dg
from . import jbi
from . import utterance as ut

HUMAN_MARK = "人が教えた（この会話の中だけ）"
AI_MARK = "AI の推定（未確定）"
DATA_CHANGED = "データが変わったので、ここからは新しい内容で答えます"
INVALIDATED = "さきほど教えてもらった意味（{text}）を確認できなくなりました。もう一度教えてください"
TAUGHT = "「{text}」を、{target}の意味として覚えました（人が教えた・この会話の中だけ。保存しません）"
REPLACED = "「{old}」を「{new}」に置き換えました（{target}。人が教えた・この会話の中だけ）"
ASK_WHICH_BLOCK = "どの動きのことですか？ 下のボタンで選んでください"
ASK_WHICH_POINT = "どの点のことですか？ 下のボタンで選ぶか、「ボタンは C00002」のように位置番号を添えて教えてください"
NO_BLOCK = "どの動きのことか、プログラムで確かめられませんでした。意味は覚えていません"
NOT_IN_BLOCK = "{pos} は、話している動き（{target}）に入っていないため、確認できません。意味は覚えていません"
NOT_IN_JOB = "{pos} は、この動作データに無いため、確認できません。意味は覚えていません"
TWO_POSITIONS = "位置番号が2つ以上あるため、どの点の意味か決められません。1つずつ教えてください"
HESITANT = "「{word}」のように確かでない言い方のため、意味としては覚えません。確かなときに、もう一度教えてください"
NEGATED = ("「{word}」という打ち消しの言葉があるため、意味としては覚えません。"
           "何をする動きか分かったら、打ち消しの言葉を使わずに教えてください")
FORBIDDEN_MEANING = "安全や距離を決めつける言葉は、意味として覚えません"
TOO_LONG_MEANING = "意味が長すぎるため、覚えません（{n}文字まで）"
NO_OLD_MEANING = "前に教えてもらった意味に「{old}」がないため、置き換えません。何をする動きか、もう一度教えてください"
MANY_OLD_MEANING = "「{old}」が入った意味が2つ以上あるため、どれを置き換えるか決められません。置き換えません"
STALE_CHOICE = "この選択肢は古くなりました（会話かデータが変わりました）。もう一度教えてください"
PENDING_EXPLAIN_ONLY = ("承認待ちの変更候補があるため、今は説明だけ答えます。変更は、候補を確かめて承認ボタンで承認するか、"
                        "元に戻してから、指示の欄で行ってください")
COUNT_NEGATED = ("「{word}」という打ち消しの言葉があるため、回数の希望としては読みません（記録もしません）。"
                 "してほしいことだけを言ってください")
COUNT_HESITANT = "「{word}」のように確かでない言い方のため、回数の希望としては読みません。決まったら、もう一度言ってください"
COUNT_MISMATCH = ("あなたの文から読んだ回数と、Claude が読んだ回数が合いません（食い違い）。記録だけにして、何も変えません。"
                  "回数を、もう一度はっきり言ってください")
COUNT_TWO = "回数が2つ以上あり、どれか決められません"
COUNT_NO_TARGET = "くり返しの区間が2つ以上あるため、どの動きの回数か決めません"
COUNT_ADD_NO_TARGET = "どの動きか決まらないため、今の回数に足した数を計算しません"
CHANGE_RECORD_NOTE = "記録だけです。変更の候補は作りません（変更は、指示の欄・範囲のボタン・承認ボタンで行います）"
PLAIN_ONE = "同じ動きを{count}回くり返しています。何をする動きか、教えてもらえますか？"
PLAIN_MANY = "くり返しの動きが{n}か所あります。何をする動きか、教えてもらえますか？"
PLAIN_NONE = "動作データの動きを読みました（くわしくは補足の行をご覧ください）。何をする動きか、教えてもらえますか？"
FIXED_QUESTIONS = "Claude の答えに質問が2つ以上あったため、表示しません（1回に1つずつ聞くようにしています）"
FIXED_JOB_WORD = "Claude の答えに「ジョブ」という言い方があったため、表示しません（「動作データ」と言うようにしています）"

MAX_MEANING = 40
MAX_BLOCKS_SCAN = 60  # 位置の数がこれより多いジョブでは区間を探さない（claude_explain.find_repeats と同じ）

_POS = re.compile(r"C[0-9]{5}")
_SEP = r"[\s、。,，.．!！]*"
BACKCHANNELS = ("うん、そうだね", "そうそう", "そうだね", "そうだよ", "そうです", "そうですね", "その通り", "そのとおり",
                "合ってる", "あってる", "合ってます", "あってます", "うんうん", "なるほど", "わかった", "分かった",
                "わかりました", "分かりました", "了解", "りょうかい", "オーケー", "OK", "ok", "Ok", "いいね", "いいよ",
                "ありがとう", "正解", "そう", "うん", "はい", "ええ", "ね", "よ")
# 相づちの後は、区切り（、。空白）か文の終わりか、ひらがなでない字（「そうボタンを…」）。「そうじ」「ねじ」は相づちにしない
_BACK_RE = re.compile(f"^(?:{'|'.join(re.escape(w) for w in sorted(BACKCHANNELS, key=len, reverse=True))})"
                      r"(?:[\s、。,，.．!！]+|$|(?=[^ぁ-ゖ]))")
HESITATION_WORDS = ("たぶん", "多分", "かもしれ", "かも", "かな", "と思う", "と思います", "とおもう", "おそらく", "恐らく",
                    "気がする", "みたい", "らしい", "はず", "だろう", "でしょう", "かしら", "わからないけど", "知らないけど")
_QUESTION_WORDS = ("何", "どこ", "どの", "どう", "どれ", "なぜ", "いつ", "教えて", "説明", "って?",
                   "とは", "ですか", "ますか")
_ENDERS = ("なんだよね", "なんだよ", "なんです", "なんだ", "んだよね", "んだよ", "んです", "のです", "のだ", "んだ",
           "だよね", "ですよ", "ですね", "だよ", "です", "だね", "だ", "よね", "よ", "ね", "の", "わ", "さ")
_TOPIC_LEAD = re.compile(r"^(?:これは|それは|あれは|ここは|そこは|この動きは|その動きは|この動作は|その動作は)")
_ACTION_END = re.compile(r"(?:ている|てる|でいる|でる|ています|てます|でいます|する|します|作業|動作|動き)$"
                         r"|を.*[うくすつぬふむゆるぐずぶ]$")
_NOT_MEANING_LEAD = re.compile(r"^(?:じゃあ|では|それじゃ|次|つぎ|ちょっと|もう一度|もう1度)")
_CORRECTION = re.compile(r"^(?P<old>.+?)(?:じゃなくて|ではなくて|でなくて|じゃなく|ではなく|でなく)、?(?P<new>.+)$")
_POINT_A = re.compile(r"^(?P<noun>.+?)(?:は|が)\s?(?P<pos>C[0-9]{5})(?:の位置|の点)?$")
_POINT_B = re.compile(r"^(?P<pos>C[0-9]{5})\s?(?:は|が)\s?(?P<noun>.+)$")
# 回数の言葉（「3回」「三回」「もう1回」「回数」）と、変える言葉
_KANJI = "〇一二三四五六七八九十"
_COUNT = re.compile(r"([0-9]+|[〇一二三四五六七八九十]+)\s?回(?!目)")
_COUNT_FROM = re.compile(r"([0-9]+|[〇一二三四五六七八九十]+)\s?回\s?から\s?([0-9]+|[〇一二三四五六七八九十]+)\s?回")
_COUNT_ADD = re.compile(r"(?:もう)?\s?([0-9]+|[〇一二三四五六七八九十]+)\s?回\s?(?:増や|ふやし|ふやす|追加|足し|たし|多く)")
_COUNT_KEEP = re.compile(r"([0-9]+|[〇一二三四五六七八九十]+)\s?回\s?(?:の)?\s?まま")
_CHANGE_WORDS = re.compile(r"回数|にして|に変更|に変え|へ変更|増や|減ら|追加|足して|変更して|変えて")
_ALL_WORDS = ("全部", "ぜんぶ", "両方", "りょうほう", "すべて", "全て", "どっちも", "どちらも", "みんな", "全体")
_FORBIDDEN_MEANING = ("安全", "危険", "干渉", "衝突", "ぶつか", "当たる", "当たら", "当たり", "接触", "距離")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _kanji_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in "〇一二三四五六七八九":
            cur = cur * 10 + "〇一二三四五六七八九".index(ch)
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        else:
            return None
    return total + cur


# ---- くり返しの区間（プログラムが見つけたもの） ----
@dataclass(frozen=True)
class Block:
    """ジョブの中に続けてそのまま書かれた区間（jbi.Job.find_repeat で数えたもの）。行番号はファイルの行番号（1から）。"""
    positions: tuple[str, ...]
    first_line: int
    count: int
    cycle_lines: tuple[str, ...]  # 1回分の命令の行（改行コードを除く）

    @property
    def last_line(self) -> int:
        return self.first_line + self.count * len(self.positions) - 1

    @property
    def key(self) -> tuple:
        return (self.positions, self.first_line, self.cycle_lines)

    def lines(self) -> list[int]:
        return list(range(self.first_line, self.last_line + 1))

    def contains(self, line: int) -> bool:
        return self.first_line <= line <= self.last_line

    @property
    def path(self) -> str:
        return "→".join(self.positions)

    @property
    def label(self) -> str:
        """補足の文（プログラムが作る）：「21〜26行目：C00001→C00002→C00003（2回）」"""
        return f"{self.first_line}〜{self.last_line}行目：{self.path}（{self.count}回）"

    @property
    def target(self) -> str:
        return f"{self.first_line}〜{self.last_line}行目の動き（{self.path}）"


def repeat_blocks(job: jbi.Job | None, limit: int = MAX_BLOCKS_SCAN) -> list[Block]:
    """続けてそのまま書かれた区間（2回以上）。claude_explain.find_repeats と同じ数え方（位置の組を試す）。"""
    if job is None or job.problems:
        return []
    names = sorted(n for n in job.positions if _POS.fullmatch(n))
    if len(names) > limit:
        return []
    out: list[Block] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                blk = job.find_repeat(a, b)
            except jbi.JbiError:
                continue
            if blk.count >= 2:
                out.append(Block(positions=tuple(blk.positions), first_line=blk.first_line + 1, count=blk.count,
                                 cycle_lines=tuple(x.rstrip("\r\n") for x in blk.cycle_lines)))
    return out


def blocks_of_lines(blocks: list[Block], lines) -> tuple[list[Block], bool]:
    """根拠の行が入っている区間と、区間の外の行があるか。"""
    hit: list[Block] = []
    outside = False
    for n in lines:
        found = [b for b in blocks if b.contains(n)]
        if not found:
            outside = True
        for b in found:
            if b not in hit:
                hit.append(b)
    return hit, outside


def discussed_block(blocks: list[Block], last_evidence) -> tuple[Block | None, list[Block]]:
    """直前の会話で話題にした区間を、プログラムが1つに決める。決められなければ (None, 選ぶ候補)。

    - 直前の返事の根拠の行があれば：その行が全部1つの区間に入っているときだけ、その区間（区間の外の行があれば決めない）
    - 根拠の行が無ければ：プログラムが見つけた区間が1つだけなら、その区間
    """
    ev = list(last_evidence or ())
    if ev:
        hit, outside = blocks_of_lines(blocks, ev)
        if len(hit) == 1 and not outside:
            return hit[0], []
        return None, list(blocks)
    if len(blocks) == 1:
        return blocks[0], []
    return None, list(blocks)


# ---- 光らせる範囲と補足（E4・E1。プログラムが行番号から決める） ----
def highlight_and_supplement(job: jbi.Job | None, evidence: list[dict]) -> tuple[dict, list[str]]:
    """根拠の行（プログラムが確かめたもの）から、光らせる行・位置と、補足の文を作る。Claude の文は使わない。"""
    if job is None or not evidence:
        return {"lines": [], "positions": []}, []
    texts = {ins.index + 1: ins for ins in job.instructions}
    blocks = repeat_blocks(job)
    lines: list[int] = []
    supplement: list[str] = []
    for ev in evidence:
        n = ev.get("line")
        if n not in texts:
            continue  # ジョブに無い行は光らせない
        hit = [b for b in blocks if b.contains(n)]
        if hit:
            for b in hit:
                if b.label not in supplement:
                    supplement.append(b.label)
                lines.extend(x for x in b.lines() if x not in lines)
        else:
            ins = texts[n]
            note = dg.evidence_note(ins.text)
            s = f"{n}行目：{ins.text}" + (f"（{note}）" if note else "")
            if s not in supplement:
                supplement.append(s)
            if n not in lines:
                lines.append(n)
    positions: list[str] = []
    for n in sorted(lines):
        for p in texts[n].refs:
            if _POS.fullmatch(p) and p in job.positions and p not in positions:
                positions.append(p)
    return {"lines": sorted(lines), "positions": positions}, supplement


# ---- E1 やさしい言葉（返事の検査） ----
_CODE_WORDS = re.compile(r"C\s?[0-9]{5}|(?<![A-Z])(?:S?MOV[LJCS]|IMOV|DOUT|TIMER)(?![A-Z])|JOB|ジョブ", re.I)
_JOB_WORD = re.compile(r"(?<![A-Z])JOB(?![A-Z])|ジョブ", re.I)


def plain_problem(reply: str, first: bool) -> str | None:
    """最初の説明（first）に、位置番号・命令の名前・「ジョブ」があるか。どの返事でも「？」は1つまで・「ジョブ」は使わない。"""
    t = _nfkc(reply)
    if t.count("?") >= 2:
        return FIXED_QUESTIONS
    if first and _CODE_WORDS.search(t):
        return "plain"
    if _JOB_WORD.search(t):
        return FIXED_JOB_WORD
    return None


def plain_fixed(facts: dict | None) -> str:
    """最初の説明の決まった文（プログラムが事実から作る。数は事実にあるものだけ）。"""
    reps = (facts or {}).get("repeats") or []
    if len(reps) == 1:
        return PLAIN_ONE.format(count=reps[0]["count"])
    if len(reps) > 1:
        return PLAIN_MANY.format(n=len(reps))
    return PLAIN_NONE


_GUESS = ("ようです", "ような", "ように見え", "らしい", "かもしれ", "と思われ", "と思います", "推定", "おそらく",
          "見られ", "考えられ", "でしょう", "のでは")


def ai_guesses(reply: str) -> list[str]:
    """Claude の返事の中の推定の言葉（文）。表示するだけで、登録はしない。"""
    out = []
    for s in re.split(r"(?<=[。！!？?])", reply):
        s = s.strip()
        if s and any(w in s for w in _GUESS):
            out.append(s)
    return out


# ---- 回数（E5。プログラムが人の文から読む） ----
@dataclass(frozen=True)
class CountReading:
    total: int | None = None      # 変更後の合計の回数（プログラムが読んだ）
    keep: bool = False            # 「2回のまま」
    reason: str | None = None     # 読まない理由
    stop: str | None = None       # 打ち消し・ためらい（Claude を呼ばずに決まった文を返す）
    target: Block | None = None
    target_note: str | None = None


def has_count_words(text: str) -> bool:
    t = _nfkc(text)
    return bool(_COUNT.search(t) or "回数" in t)


def _negation_word(text: str) -> str | None:
    f = ut.read_utterance(text)
    words = list(f.negations) + list(f.undo_negations)
    return words[0] if words else None


def _hesitation_word(text: str) -> str | None:
    t = _nfkc(text)
    return next((w for w in HESITATION_WORDS if w in t), None)


def read_count(text: str, blocks: list[Block], last_evidence=()) -> CountReading | None:
    """人の文の回数。回数の言葉が無ければ None。"""
    if not has_count_words(text):
        return None
    t = _nfkc(text)
    target, _ = discussed_block(blocks, last_evidence)
    if len(blocks) >= 2 and any(w in t for w in _ALL_WORDS):
        target = None  # 「全部」「両方」：区間が2つ以上なら、直前に話した区間に決めない
    note = None
    if target is None:
        note = COUNT_NO_TARGET if len(blocks) >= 2 else NO_BLOCK
    neg = _negation_word(text)
    if neg:
        return CountReading(reason=COUNT_NEGATED.format(word=neg), stop=COUNT_NEGATED.format(word=neg),
                            target=target, target_note=note)
    hes = _hesitation_word(text)
    if hes:
        return CountReading(reason=COUNT_HESITANT.format(word=hes), stop=COUNT_HESITANT.format(word=hes),
                            target=target, target_note=note)
    m = _COUNT_FROM.search(t)
    if m:
        return CountReading(total=_kanji_int(m.group(2)), target=target, target_note=note)
    m = _COUNT_KEEP.search(t)
    if m:
        return CountReading(total=_kanji_int(m.group(1)), keep=True, target=target, target_note=note)
    m = _COUNT_ADD.search(t)
    if m:
        add = _kanji_int(m.group(1))
        if target is None or add is None:
            return CountReading(reason=COUNT_ADD_NO_TARGET, target=None, target_note=note)
        return CountReading(total=target.count + add, target=target, target_note=note)
    nums = {_kanji_int(x) for x in _COUNT.findall(t)}
    nums.discard(None)
    if len(nums) >= 2:
        return CountReading(reason=COUNT_TWO, target=target, target_note=note)
    if len(nums) == 1:
        return CountReading(total=nums.pop(), target=target, target_note=note)
    return CountReading(reason="回数が読めません", target=target, target_note=note)


def change_record(reading: CountReading | None, intent: str | None, change: dict | None) -> dict | None:
    """変更の希望の記録（表示だけ）。プログラムが読んだ回数と、Claude の change_request の数を並べ、違えば食い違い。"""
    claude_repeat = change if (intent == "change_request" and change and change.get("kind") == "repeat") else None
    if reading is None and claude_repeat is None:
        return None
    prog_total = reading.total if reading else None
    keep = bool(reading and reading.keep)
    claude_total = claude_repeat.get("to_count") if claude_repeat else None
    if reading is None or claude_repeat is None:
        mismatch = claude_repeat is not None and reading is None
    elif keep:
        cur = reading.target.count if reading.target else None
        mismatch = claude_total is not None and claude_total != (cur if cur is not None else prog_total)
    else:
        mismatch = claude_total != prog_total
    target = reading.target if reading else None
    return {
        "kind": "repeat",
        "program_total": prog_total, "program_keep": keep,
        "program_reason": reading.reason if reading else "人の文に回数の言葉がありません",
        "claude_total": claude_total, "claude_from": claude_repeat.get("from_count") if claude_repeat else None,
        "mismatch": bool(mismatch),
        "target": target.label if target else None,
        "target_note": (reading.target_note if reading else None) if target is None else None,
        "note": CHANGE_RECORD_NOTE,
    }


def looks_like_change(text: str) -> bool:
    """変更の希望のような文か（回数・移動・速度・比べる・元に戻す・承認の言葉）。承認待ちのときに Claude へ送らないため。"""
    if has_count_words(text) or _CHANGE_WORDS.search(_nfkc(text)):
        return True
    f = ut.read_utterance(text)
    return any(k in ut.OPS for k in f.kinds) or bool(f.stop_reasons)


# ---- 人が教えた意味（E2） ----
@dataclass(frozen=True)
class Meaning:
    text: str                      # 人の文から取った意味（Claude の言い換えではない）
    block: tuple                   # 区間の key（positions, first_line, cycle_lines）。回数には依らない
    position: str | None = None    # 点の意味（「ボタンは C00002」）。区間の動きの意味なら None
    said: str = ""                 # 人の文（そのまま）

    def target_text(self, b: Block) -> str:
        return f"{self.position} の点（{b.target}の中）" if self.position else b.target


def _find_block(blocks: list[Block], key: tuple) -> Block | None:
    positions, first_line, cycle = key
    return next((b for b in blocks if b.positions == positions and b.first_line == first_line
                 and b.cycle_lines == cycle), None)


def verify(m: Meaning, job: jbi.Job | None) -> Block | None:
    """意味の対象が、今のデータにも同じ位置番号・同じ行の並びで残っているか（プログラムの照合）。残っていればその区間。

    回数が変わっても（2回→3回）、同じ区間（同じ位置の並び・同じ1回分の行・同じ始めの行）が見つかれば、
    足した回は1回目の写しなので、その回にも当たる。位置の値（座標）は見ない。
    """
    b = _find_block(repeat_blocks(job), m.block)
    if b is None:
        return None
    if m.position is not None and m.position not in b.positions:
        return None
    return b


@dataclass
class Outcome:
    """人の文を見た結果。kind：
    pass（Claude に送る。登録しない）／taught（登録した。Claude にも送る）／replaced（置き換えた。Claude にも送る）／
    ask（どれか聞く。Claude に送らない）／refused（登録しない理由。Claude に送らない）"""
    kind: str
    message: str | None = None
    meaning: Meaning | None = None
    category: str = "other"   # question・change・backchannel・hesitant・negated・correction・meaning・other


def _strip_back(t: str) -> str:
    prev = None
    while prev != t:
        prev = t
        t = _BACK_RE.sub("", t, count=1).strip()
    return t


def _strip_end(t: str) -> tuple[str, bool]:
    t = re.sub(r"[\s、。,，.．!！]+$", "", t)
    had = False
    changed = True
    while changed:
        changed = False
        for e in _ENDERS:
            if t.endswith(e) and len(t) > len(e):
                t = t[: -len(e)].rstrip("、, ")
                had = changed = True
                break
    return t, had


def _is_question(t: str) -> bool:
    return "?" in t or any(w in t for w in _QUESTION_WORDS)


def classify(text: str) -> tuple[str, dict]:
    """人の文の種類（プログラムが決める）。返す値：(種類, 取り出したもの)。"""
    t = _nfkc(text).strip()
    if not t:
        return "other", {}
    if _is_question(t):
        return "question", {}
    if has_count_words(t) or _CHANGE_WORDS.search(t):
        return "change", {}
    rest = _strip_back(t)
    if not rest or not re.search(r"[ぁ-ゖァ-ヺ一-龥A-Za-z]", rest):
        return "backchannel", {}
    hes = _hesitation_word(rest)
    if hes:
        return "hesitant", {"word": hes}
    core, had_end = _strip_end(rest)
    m = _CORRECTION.match(core)
    if m:
        old = _strip_end(m.group("old").strip("、, "))[0]
        new = _strip_end(m.group("new").strip("、, "))[0]
        if old and new and not _negation_word(new) and not _POS.search(ut.normalize(old + new)):
            return "correction", {"old": old, "new": new}
    neg = _negation_word(rest)
    if neg:
        return "negated", {"word": neg}
    if not core or _NOT_MEANING_LEAD.match(core):
        return "other", {}
    norm = ut.normalize(core)
    positions = list(dict.fromkeys(_POS.findall(norm)))
    topic = bool(_TOPIC_LEAD.match(core))
    body = _TOPIC_LEAD.sub("", core).strip()
    if positions:
        if len(positions) >= 2:
            return "meaning", {"positions": positions}
        for pat in (_POINT_A, _POINT_B):
            mm = pat.match(_nfkc(body).replace("ｃ", "C").replace("c", "C"))
            if mm:
                noun = _strip_end(mm.group("noun").strip())[0]
                if noun and not _POS.search(ut.normalize(noun)):
                    return "meaning", {"text": noun, "position": ut.normalize(mm.group("pos")), "point": True}
        return "meaning", {"positions": positions}
    if not body or len(body) < 2:
        return "other", {}
    action = bool(_ACTION_END.search(body))
    if not (had_end or topic or action):
        return "other", {}
    return "meaning", {"text": body, "point": not action}


@dataclass
class Pending:
    """「どの動きのことですか？」の聞き返し（人がボタンで選ぶ）。"""
    token: int
    text: str
    said: str
    kind: str                           # "block" か "point"
    options: list[tuple[str, str, tuple, str | None]]  # (id, 表示, 区間の key, 点)


@dataclass
class TalkMemory:
    """この画面の会話の中だけの、人が教えた意味（保存しない）。"""
    content_hash: str
    meanings: list[Meaning] = field(default_factory=list)
    pending: Pending | None = None
    notices: list[str] = field(default_factory=list)
    signature: tuple | None = None
    last_evidence: tuple[int, ...] = ()
    _token: int = 0

    # ---- データが変わったか（候補・承認・ジョブ） ----
    def sync(self, signature: tuple, current: jbi.Job | None) -> bool:
        """データの状態が変わったら、意味と対象の対応を確かめ直す。確かめられない意味は無効にして知らせる。変わったら True。"""
        if self.signature is not None and len(self.signature) == len(signature) and all(
                a is b or (isinstance(a, str) and a == b) for a, b in zip(self.signature, signature)):
            return False
        first = self.signature is None
        self.signature = signature
        if first:
            return False
        self.pending = None
        self.last_evidence = ()
        self.recheck(current)
        return True

    def recheck(self, current: jbi.Job | None) -> None:
        keep = []
        for m in self.meanings:
            if verify(m, current) is not None:
                keep.append(m)
            else:
                self.notices.append(INVALIDATED.format(text=m.text))
        self.meanings = keep

    def take_notices(self) -> list[str]:
        out, self.notices = self.notices, []
        return out

    def clear(self) -> None:
        self.meanings, self.pending, self.last_evidence = [], None, ()

    # ---- 人の文を見る ----
    def consider(self, text: str, original: jbi.Job | None, current: jbi.Job | None) -> Outcome:
        """人の文から、意味を登録するか決める（プログラムだけで決める）。"""
        kind, got = classify(text)
        if kind in ("question", "change", "backchannel", "other"):
            return Outcome("pass", category=kind)
        if kind == "hesitant":
            return Outcome("refused", HESITANT.format(word=got["word"]), category=kind)
        if kind == "negated":
            return Outcome("refused", NEGATED.format(word=got["word"]), category=kind)
        if kind == "correction":
            return self._correct(text, got["old"], got["new"], current)
        # meaning
        if "positions" in got:
            return Outcome("refused", TWO_POSITIONS if len(got["positions"]) >= 2 else ASK_WHICH_POINT,
                           category=kind)
        mtext = got["text"]
        why = _meaning_problem(mtext)
        if why:
            return Outcome("refused", why, category=kind)
        blocks = repeat_blocks(original)
        target, options = discussed_block(blocks, self.last_evidence)
        if got.get("point"):
            pos = got.get("position")
            if pos is None:
                if target is None:
                    return self._ask_block(text, mtext, options, "point") if options else Outcome(
                        "refused", NO_BLOCK, category=kind)
                return self._ask_point(text, mtext, target)
            if original is None or pos not in original.positions:
                return Outcome("refused", NOT_IN_JOB.format(pos=pos), category=kind)
            if target is None:
                inside = [b for b in blocks if pos in b.positions]
                if len(inside) != 1:
                    return Outcome("refused", NOT_IN_BLOCK.format(pos=pos, target="くり返しの区間"), category=kind)
                target = inside[0]
            if pos not in target.positions:
                return Outcome("refused", NOT_IN_BLOCK.format(pos=pos, target=target.target), category=kind)
            return self._register(Meaning(mtext, target.key, pos, text), current)
        if target is None:
            if not options:
                return Outcome("refused", NO_BLOCK, category=kind)
            return self._ask_block(text, mtext, options, "block")
        return self._register(Meaning(mtext, target.key, None, text), current)

    def _register(self, m: Meaning, current: jbi.Job | None) -> Outcome:
        b = verify(m, current)
        if b is None:
            return Outcome("refused", NO_BLOCK, category="meaning")
        # 同じ対象の前の意味は置き換える（人が言い直した）
        self.meanings = [x for x in self.meanings if not (x.block == m.block and x.position == m.position)]
        self.meanings.append(m)
        self.pending = None
        return Outcome("taught", TAUGHT.format(text=m.text, target=m.target_text(b)), m, category="meaning")

    def _correct(self, said: str, old: str, new: str, current: jbi.Job | None) -> Outcome:
        why = _meaning_problem(new)
        if why:
            return Outcome("refused", why, category="correction")
        hits = [m for m in self.meanings if old in m.text]
        if not hits:
            return Outcome("refused", NO_OLD_MEANING.format(old=old), category="correction")
        if len(hits) >= 2:
            return Outcome("refused", MANY_OLD_MEANING.format(old=old), category="correction")
        m = hits[0]
        b = verify(m, current)
        if b is None:
            return Outcome("refused", NO_BLOCK, category="correction")
        new_text = m.text.replace(old, new, 1)
        if len(new_text) > MAX_MEANING:
            return Outcome("refused", TOO_LONG_MEANING.format(n=MAX_MEANING), category="correction")
        nm = Meaning(new_text, m.block, m.position, said)
        self.meanings = [nm if x is m else x for x in self.meanings]
        return Outcome("replaced", REPLACED.format(old=m.text, new=new_text, target=nm.target_text(b)), nm,
                       category="correction")

    def _next_token(self) -> int:
        self._token += 1
        return self._token

    def _ask_block(self, said: str, mtext: str, blocks: list[Block], kind: str) -> Outcome:
        opts = [(f"block:{i}", b.label, b.key, None) for i, b in enumerate(blocks)]
        self.pending = Pending(self._next_token(), mtext, said, "block" if kind == "block" else "block_then_point", opts)
        return Outcome("ask", ASK_WHICH_BLOCK, category="meaning")

    def _ask_point(self, said: str, mtext: str, b: Block) -> Outcome:
        opts = [(f"pos:{p}", f"{p}（{b.first_line}〜{b.last_line}行目の中）", b.key, p) for p in b.positions]
        self.pending = Pending(self._next_token(), mtext, said, "point", opts)
        return Outcome("ask", ASK_WHICH_POINT, category="meaning")

    def choose(self, choice: object, token: object, original: jbi.Job | None, current: jbi.Job | None) -> Outcome:
        """人がボタンで選んだ対象で登録する。番号（token）と選択肢が今のものと合うときだけ。"""
        p = self.pending
        if (p is None or isinstance(token, bool) or not isinstance(token, int) or token != p.token
                or not isinstance(choice, str)):
            return Outcome("refused", STALE_CHOICE)
        opt = next((o for o in p.options if o[0] == choice), None)
        if opt is None:
            return Outcome("refused", STALE_CHOICE)
        _, _, key, pos = opt
        b = _find_block(repeat_blocks(original), key)
        if b is None:
            self.pending = None
            return Outcome("refused", STALE_CHOICE)
        if p.kind == "block_then_point":  # 点の意味：区間を決めたので、次はどの点か
            return self._ask_point(p.said, p.text, b)
        return self._register(Meaning(p.text, key, pos, p.said), current)

    # ---- Claude に渡す事実・画面に出すもの ----
    def facts_for_claude(self, original: jbi.Job | None) -> list[dict]:
        out = []
        blocks = repeat_blocks(original)
        for m in self.meanings:
            b = _find_block(blocks, m.block)
            if b is None:
                continue
            out.append({"target": m.target_text(b), "meaning": m.text, "lines": [b.first_line, b.last_line],
                        "note": "人が教えた（この会話の中だけ）。変更してよい範囲・承認には使わない"})
        return out

    def state(self, current: jbi.Job | None) -> dict:
        items = []
        for m in self.meanings:
            b = verify(m, current)
            if b is None:
                continue
            items.append({"text": m.text, "target": m.target_text(b), "position": m.position,
                          "lines": b.lines() if m.position is None else [], "mark": HUMAN_MARK, "by": "human"})
        p = self.pending
        return {
            "meanings": items,
            "pending": ({"token": p.token, "text": p.text,
                         "question": ASK_WHICH_POINT if p.kind == "point" else ASK_WHICH_BLOCK,
                         "options": [{"id": o[0], "label": o[1]} for o in p.options]} if p else None),
            "human_mark": HUMAN_MARK, "ai_mark": AI_MARK,
        }


def _meaning_problem(text: str) -> str | None:
    if any(w in text for w in _FORBIDDEN_MEANING) or ut.contains_forbidden(text):
        return FORBIDDEN_MEANING
    if len(text) > MAX_MEANING:
        return TOO_LONG_MEANING.format(n=MAX_MEANING)
    return None
