"""画面の一連の流れ（シミュレーションモード）：読込 → 位置選択 → 指示 → 変更候補 → プレビュー → ボタン承認 → 出力。

- 画面ごとの Session はメモリの中だけに持つ（ディスクに保存しない）。数を超えたら古いものから捨てる。
- 指示（hear）は Session.hear → 解釈の係（本物の Claude か偽のClaude。claude_interpreter.from_env）→ Session の select / propose / compare / undo。
  指示の経路からは承認も出力も呼ばない（「承認」「OK」などの発話は Session が止める）。
- 承認（approve）は画面の承認ボタンからだけ呼ぶ。sim.approve を使い、承認した人の名前が要る。
- 出力（export）は sim.export_sim（合成ジョブだけ）。失敗は理由を返し、成功と表示させない。
- 承認の後で新しい指示・元に戻す・位置の選択があったら、承認を取り消す。
- 実機用の出力・転送・ロボットの起動は無い。
- 承認待ちの候補があるときの移動・速度の指示は、解釈の係（Claude）を呼ぶ前に断る（呼ばない）。
- Claude との会話（「置くところを少し手前にして」。voice_teaching/dialog.py）：場所の言葉があれば、位置の候補を
  解釈の係に頼み（合成ジョブだけ）、プログラムが照合して、人がボタン（か短い肯定）で選ぶ。方向は6つのボタン、
  量は人の言葉。集めたことは決まった文（「C00005をYのマイナス3ミリ」）にして、ふつうの指示と同じ流れに通す。
  会話の選択肢は choose（/api/dialog）で受け取り、今の選択肢の番号（turn）と合わなければ使わない。
- 上下の向き（CHECKER_026 M2・11章）：毎回の指示・承認・出力・状態の表示のときに、上下の向きの設定（updown_config）を
  読み直してジョブと照合する。照合に通ったときだけ「上・下」を Z± にし、方向のボタンに「（上）」「（下）」を付ける。
  候補を作ったときの照合の結果と、承認・出力のときの結果が違えば（設定が1バイトでも変われば）、承認を消して断る。
  承認の記録と作業ログに、使った設定の指紋と確認した人を残す。設定は読むだけ（画面・API・声からは書かない）。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .. import claude_explain, claude_interpreter, claude_lead, jbi, mock_claude, sim
from .. import nl_understand as nu
from .. import stale
from .. import claude_talk as talk
from .. import repeat_count as rc
from .. import dialog as dg
from .. import updown as ud
from .. import utterance as ut
from ..session import APPROVED_PENDING_MESSAGE, NO_POSITION, PENDING_MESSAGE, Comparison, Reply, Session
from . import api

MAX_SESSIONS = 8
MAX_TEXT = 500
STEP_F_RANGE_NOTE = ("範囲を確かめてください。［この範囲を繰り返す］を押すと、変更の候補になります"
                     "（承認は、そのあとの確認表示のボタンで行います）")
# Step F：承認・出力・転送・起動など、ボタンだけで行う操作の言葉。会話の道（Claude）へは送らず、指示の道の決まった文で止める
_BUTTON_ONLY_WORDS = re.compile(r"承認|出力|書き出|転送|送信|保存|起動|実行|スタート|エクスポート|ダウンロード|登録|上書き|確定")
_QUESTION_WORDS = re.compile(r"[?？]|何|なに|どこ|どう|どれ|なぜ|教えて|説明")
# CHECKER_033 P1-B（11/4）：1つの文に2つ以上の変更（「3回にして、あと3ミリ下げて」）は、どれも受け付けない
# （前半だけ・後半だけを黙って受けない）。候補・範囲の確認・承認は変えず、Claude も呼ばない
STEP_F_ONE_AT_A_TIME = ("変更は1つずつ指示してください。この文には変更が2つ以上あるため、どれも受け付けていません"
                        "（候補・承認の状態は変えていません）")
# 唐川さんの決定（11/4）：「登録」「保存」は、作業目的の意味付け（「ボタンを押す作業として登録して」）と、ジョブの変更・出力を分ける
STEP_F_PURPOSE_OR_CHANGE = ("作業の目的を教えるのか、動作データを変えるのかが分かりませんでした。何も変えていません。"
                            "目的を教えるときは「ボタンを押す作業です」のように、変更は1つずつ指示してください")
_PURPOSE_SAVE = re.compile(r"として(?:登録|保存|覚え)|と(?:登録|覚え)|の(?:作業|動き|動作)(?:です|だよ|だ)?(?:と|として)?(?:登録|保存|覚え)")
_OUTPUT_WORDS = re.compile(r"承認|出力|書き出|転送|送信|起動|実行|スタート|エクスポート|ダウンロード|上書き|確定")
# P1-C（11/5）：回数の文で、人が位置・範囲を言った（「C00002からC00003を3回」）。プログラムが見つけた区間の決まった文に
# 置き換えない（言った範囲が勝手に C00001〜C00003 に広がらない）。指示の道が、言ったとおりの範囲だけを調べる
# （区間と合わなければ止める）。「2回から3回に」の「から」は範囲ではないので、先に外して調べる
_RANGE_SAID = re.compile(r"C[0-9]|[0-9]+(?:番|行|点|か所|箇所|個目|つ目)|から|〜|~|まで|位置|ポイント")
_COUNT_FROM_WORDS = re.compile(r"[0-9]+回から")


def _said_range(text: str) -> bool:
    """回数の文に、人が言った位置・範囲（C の番号・「から」「〜」「まで」・「2番」など）があるか。"""
    return bool(_RANGE_SAID.search(_COUNT_FROM_WORDS.sub("", rc.to_digits(text))))


def _status_line(has_candidate: bool, blocked: bool, approved: bool, exported: bool, range_pending: bool,
                 hold: bool = False, voided_why: str | None = None) -> str:
    """いまの状態（プログラムの確定情報だけ）。"""
    if range_pending:
        cand = "範囲の確認中（まだ候補はありません）"
    elif not has_candidate:
        cand = f"なし（前の候補は{voided_why}のため取り消しました）" if voided_why else "なし"
    elif hold:
        cand = "保留（取り消すか確かめています。承認・出力はできません）"
    elif blocked:
        cand = "参考の表示だけ（変更しません・承認できません）"
    else:
        cand = "あり（まだ動作データは変えていません）"
    # 承認の記録があっても保留中は「済み」と言わない（UI-1・11/6。文だけ。承認・出力の判定は変えない）
    appr = ("保留中（承認・出力はできません）" if hold else "済み（ボタンで承認）") if approved else "まだ"
    return (f"アプリの記録：変更の候補 {cand}／承認 {appr}"
            f"／出力 {'済み（シミュレーション用）' if exported else 'まだ'}")


# K2（11/6）：「このあとどうすればいい？」「使い方は？」：Claude に送らず、今の状態に合わせた決まった文で答える（状態は変えない）
_NEXT_Q = re.compile(r"(?:この)?(?:あと|後|次|つぎ)(?:は|に)?(?:何|なに|どう)|どうやって使|使い方|どう使|どうすればいい|どうしたらいい"
                     r"|何をすればいい|なにをすればいい|何をしたらいい|なにをしたらいい")
M_NEXT_START = ("まず、右の図の点か下の番号で、変えたい位置を選んでください。そのあと「Zプラス○ミリ」のように、"
                "軸と向きと量を一緒に言ってください。動きの説明を聞きたいときは「何をする動き？」と聞いてください")
M_NEXT_SELECTED = ("選んだ位置（{pos}）を動かすなら、「Zプラス○ミリ」のように、軸と向きと量を一緒に言ってください。"
                   "別の位置にするときは、図の点か下の番号で選び直してください")
M_NEXT_CANDIDATE = ("今の変更候補（{summary}）を、確認表示で確かめてください。よければ承認ボタンを押します。"
                    "やめる場合は［元に戻す］を押すか「取り消して」と言ってください")
# K3（11/6）：数だけ（「3」）：何を言えばよいかを案内する（人の数を例に使わない。候補は作らない）
_BARE_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?[？?。！!]*")
# D3（11/6）：承認のあとの否定の言葉。保留のうえで、承認も取り消す（声で承認しない・古い承認を使わない）
_NEGATION_ONLY = re.compile(r"だめ|ダメ|駄目|ノー|^NO[!！。]*$|違う|ちがう|違います|出さないで|やめ|キャンセル|きゃんせる|不要|いらない")
# XC-02（11/7）：それだけの文の否定（正規化したあと、文の終わりの記号を外して全体が一致するときだけ）
_NEGATION_WHOLE = re.compile(r"(?:いや、?)?(?:だめ|ダメ|駄目|ノー|NO|いいえ|違う|ちがう|違います|出さないで|出力しないで|"
                             r"あかん|それじゃない|それじゃなくて|やだ|嫌|不要|いらない|きゃんせる|キャンセル|いや|イヤ)(?:です|だ|だよ|だね|でしょ|かな|かも)?")
def _bare(text: str) -> str:
    """否定・ためらいの言葉を全体で比べるための形（正規化して、文の終わりの記号と空白を外す。長音「ー」は外さない）。"""
    return re.sub(r"[\s。、．，,.!！?？…〜~]+$", "", ut.normalize(text).strip())


# XC-03（11/7）：承認のあとのためらい（「どう〜」の形の、決めかねている言葉）
_HESITATION = re.compile(r"どうしよ|どうかな|どうだろ|どうしたもの|どうするか")
M_Q_ANSWER = ("確かめたいのですね。今の変更候補は {summary} です。図と変更前→変更後の数で確かめてください"
              "（図は目安で、当たらないことの保証ではありません）。決めるのは画面のボタンだけです。")
M_ALREADY_EXPORTED = ("この承認の変更は、もう出力しました（{name}）。同じ中身をもう一度は出力しません。"
                      "別の変更をするときは、新しく指示してください")
M_BARE_NUMBER = ("数だけでは、何を変えるか決まりません。位置を動かすなら、軸と向きと量を一緒に言ってください（例：「Zプラス○ミリ」）。"
                 "回数を変えるなら「○回にして」と言ってください")
# K5（11/6）：回数を断ったとき、Claude の受け答えの代わりに出す文（理由と次にすることは、プログラムの断りの文が出す）
M_COUNT_REFUSED_LEAD = "回数の変更のご希望ですね。この回数にはできません（理由は次の文です）"
STEP_F_APPROVED_NEXT = "承認済みです。出力は画面の［シミュレーション用に出力］のボタンで行います（声や文字では出力しません）"
SOURCES = ("voice", "text")
NO_SESSION = "このジョブの画面の情報がありません（時間が経って捨てたか、サーバーを起動し直しました）。ジョブを読み込み直してください"
# 読み込みで閉じた画面への要求を断る決まった文（唐川さんの決定 Q-7。11/5）。閉じた画面では状態を変えない
SESSION_CLOSED = ("この画面の作業は終わりました（新しいジョブが読み込まれたため）。変更候補・承認は使えません。"
                  "新しい画面で続けてください")
_CLOSED_KEEP = 64  # 閉じた画面の番号を覚えておく数（断る文を選ぶためだけ。覚えていなくても断る）
NOT_DECIDED = "指示から変更の内容を決められませんでした。位置番号・方向・量（mm）を、もう一度指示してください"
APP_SPEAKER = "アプリ"  # プログラムの決まった文の話し手（Claude の文ではない）
NOT_APPROVED = "承認されていないため、出力しません（承認は確認表示の承認ボタンで行います）"
UPDOWN_CHANGED_APPROVE = ("変更候補を作ったあとで、上下の向きの設定（または照合の結果）が変わったため、承認できません。"
                          "元に戻して、もう一度指示してください")
UPDOWN_CHANGED_EXPORT = ("承認のあとで、上下の向きの設定（または照合の結果）が変わったため、出力しません。"
                         "承認を取り消しました")
UPDOWN_CHANGED_WHY = "上下の向きの設定（または照合の結果）が変わったため"
# 承認のあとの新しい指示で、承認と一緒に変更候補も捨てたときの文（元に戻して・キャンセル・別の位置の選択）。
# 決まった文（APPROVED_PENDING_MESSAGE）は候補が残るときだけ使う。この文も唐川さんの決定の文（一字一句このまま）
APPROVAL_AND_CANDIDATE_CANCELLED = "前の承認と変更候補を取り消しました。元のJOBは変更されていません。"
# 候補を捨てたことを言う Session の返事の文（APPROVAL_AND_CANDIDATE_CANCELLED を付けるときは、重ねないよう外す）
_DROPPED_PHRASES = ("変更候補を捨てました。元のジョブは変わっていません", "。別の位置の変更候補は捨てました（元のジョブは変わっていません）")
# 上下の向きの設定ファイルの場所の既定。None ＝ 設定なし（未確認。上・下を Z にしない）。
# サーバーは起動オプション（--updown-config。既定 records/updown.json）の場所を渡す。テストでは一時フォルダの設定に置き換える
DEFAULT_UPDOWN_CONFIG: "str | Path | None" = None
USE_DEFAULT = object()  # updown_config を渡さなかった印（DEFAULT_UPDOWN_CONFIG を使う）
# 承認のあとの3つの状態の決まった文（台本 Ver.6.1 Scene 3-3・チャッピーの修正③）。「変更できた」とは言わない
ORIGINAL_UNCHANGED = "変わっていません"
CANDIDATE_APPROVED = "人が承認しました（シミュレーション用）"
NOT_SENT = "していません"  # このアプリにはロボットへ送る・転送する処理が無い
ORIGINAL_UNKNOWN_NOTE = "元の動作データが読み込んだときと同じか確かめられないため、「元の動作データ」の行は出しません"
EXPORTED_LINE = "シミュレーション用に出力しました（{name}。実機には入れないでください）"
_UPDOWN_STATUS_NAMES = {"unconfirmed": "未確認", "mismatch": "不一致", "changed": "変更済み"}
# P0 H02・H16（docs/DESIGN_P0_STALE_CANDIDATES_20261105.md 4-6）：取消・言い直しで前の候補を無効にしたときの決まった文。
# {summary} は候補からプログラムが作る。安全や距離を断定する言葉は使わない（K9）
M_CORR = "言い直しを受け取ったため、前の変更候補（{summary}）を取り消しました。承認・出力はできません。元の動作データは変わっていません。"
M_CORR_COUNT = "{n}回にする場合は、「{n}回にして」と言い直してください。まだ新しい変更候補は作っていません"
M_CORR_SAME = "動作データでは今{n}回です。{n}回のままなら、変更はありません"
M_CORR_DECIMAL = "回数は整数で言ってください。まだ新しい変更候補は作っていません"
M_CORR_MOVE = "新しい内容は、位置・方向・量（mm）をそろえて、もう一度指示してください。まだ新しい変更候補は作っていません"
M_CORR_OTHER = "新しい内容を、もう一度指示してください。まだ新しい変更候補は作っていません"
M_HAD_APPROVAL = "前の承認も取り消しました。"
M_HAD_EXPORT = "出力したファイル（{name}）は、そのまま残っています（実機には入れないでください）。"
M_CXEX = "前の変更候補（{summary}）を取り消しました。元の動作データは変わっていません。続けて、読み込んだ元の動作データについて答えます"
M_CXEX_NOASK = ("前の変更候補（{summary}）を取り消しました。元の動作データは変わっていません。"
                "説明は、もう一度「何をしているか教えて」と聞いてください")
M_HOWCX = ("取り消す場合は、［元に戻す］を押すか「取り消して」と言ってください。取り消しても元の動作データは変わりません。"
           "今の変更候補はそのままです")
M_CORR_REMADE = "言い直しを受け取ったため、前の変更候補（{summary}）を取り消しました。承認・出力はできません。新しい指示として読みます。"
M_ASK_REVOKED = "前の承認を取り消しました。"
M_ASK_HOLD = ("この欄は説明だけで、変更はしません。変更の指示は、指示の欄（マイク・文字）でどうぞ。{revoked}"
              "今の変更候補（{summary}）を保留にしました（承認・出力はできません）。残す場合は［このまま残す］を押してから、"
              "承認ボタンでもう一度承認してください。やめる場合は［取り消す］を押してください")
M_HOLD_REVOKED = "新しい変更の指示かもしれないため、前の承認を取り消しました。"  # Q-3 の保留で承認を取り消したとき（文の頭）
M_UNSURE = ("今の変更候補（{summary}）を取り消しますか？ 取り消す場合は、［元に戻す］を押すか「取り消して」と言ってください。"
            "残す場合は「このままでいい」と言ってください。確かめるまで、承認・出力はできません")
# K1（11/6 唐川さんの許可）：「もうちょっと」「もう少し上」など、量・向きが足りない変更の意思。前の値・軸は案内に使わない
M_MORE_SHIFT = ("量や向きが決まっていないため、今の変更候補（{summary}）を保留にしました（承認・出力はできません）。"
                "どのくらい動かしますか？ 軸と向きと量を一緒に、もう一度言ってください（例：「Zプラス○ミリ」）。"
                "今のままでよければ［このまま残す］を押してから、承認ボタンでもう一度承認してください")
M_MORE_REPEAT = ("回数が決まっていないため、今の変更候補（{summary}）を保留にしました（承認・出力はできません）。"
                 "回数を変える場合は、数で言ってください（例：「○回にして」）。"
                 "今のままでよければ［このまま残す］を押してから、承認ボタンでもう一度承認してください")
M_KEPT = "今の変更候補（{summary}）を残しました。承認はまだです。内容を確かめてから、確認表示の承認ボタンで承認してください"
M_KEPT_APPROVAL = "前の承認は取り消しました。"
M_HOLD = "取り消すか確かめている変更候補は、承認・出力できません。「取り消して」か「このままでいい」で答えてください"
M_NOGEN = "画面の変更候補の番号がないため、承認・出力できません。画面を読み直してから、もう一度ボタンを押してください"
M_STALEGEN = ("画面に出ていた変更候補は、もう使えません（取り消し・言い直し・読み直しのどれかがありました）。"
              "今の内容を確かめてから、もう一度ボタンを押してください")
M_VOIDED = "（{why}のため、前の変更候補 {summary} は取り消してあります）"


def _complete_instruction(text: str) -> bool:
    """仕様 A：新しい指示として、プログラムの読みで対象・軸・向き・量がそろっているか（そろわなければ聞き返す。推測しない）。
    位置の移動：軸と向き（軸の文字＋符号、または上下）と mm の量。回数の変更：回数の変更の頼みとして読める。"""
    if rc.is_repeat_request(text):
        return True
    f = ut.read_utterance(text)
    if f.unclear_amounts or f.negations or f.unknown_words or len(f.amounts) != 1:
        return False
    a = f.amounts[0]
    if a.unit != "mm":
        return False
    has_dir = bool(f.vertical) or (bool(f.axes) and (bool(f.axis_signs) or a.sign is not None))
    return has_dir


_ONE_LIMIT = re.compile(r"^移動量 -?[0-9.]+mm は仮の上限 ([0-9.]+)mm")


def _limit_next(msg: str) -> str:
    """K4：上限を超えて止めた文に、次にすることを足す（数は止めた文の中のプログラムの数だけ）。上限の文でなければそのまま。"""
    m = _ONE_LIMIT.match(msg or "")
    if m:
        return f"{msg}。{m.group(1)}ミリ以下の量で、もう一度言ってください"
    if "累積の仮の上限" in (msg or "") or "丸めた後の実際の移動量が仮の上限" in (msg or ""):
        return f"{msg}。量を減らして、もう一度言ってください"
    return msg


def _summary(ch) -> str:
    """候補の要約（プログラムの数のまま）。回数：「C00001〜C00003 の作業を 2回 → 3回」。移動：「C00005 の Y を 150.0 → 140.0」。"""
    if ch is None:
        return "なし"
    if getattr(ch, "kind", None) == "repeat":
        return f"{ch.start}〜{ch.end} の作業を {ch.before_count}回 → {ch.after_count}回"
    target = str(getattr(ch, "target", "") or "")
    parts = target.split()
    if len(parts) == 2:
        return f"{parts[0]} の {parts[1]} を {ch.before_value} → {ch.after_value}"
    return target or "変更の候補"


@dataclass
class _Entry:
    session: Session
    sim_error: str | None = None                  # シミュレーションの候補も作れなかった理由（最後の1つ）
    comparison: tuple | None = None               # (候補, Comparison) 候補ごとの比較
    sim_proposal: sim.SimProposal | None = None
    approval: sim.Approval | None = None
    approved_candidate: object | None = None      # 承認したときの候補（同じものか確かめる）
    approval_updown: ud.UpdownStatus | None = None  # 承認したときの上下の向きの照合の結果
    # 最後に承認を消した理由が、上下の向きの設定（照合の結果）の変更か。出力の断り文に使う（P2-3）。
    # 承認したとき・ほかの理由で承認を消したとき・新しい指示・選び直し・元に戻すで False に戻す
    updown_cancelled: bool = False
    exported: str | None = None
    loaded_sha256: str | None = None              # 読み込んだときのバイト列の sha256（承認のあとの「元の動作データ：変わっていません」に使う）
    dialog: dg.Dialog = field(default_factory=dg.Dialog)  # Claude との会話の状態（メモリの中だけ。保存しない）
    frame: object | None = None                   # 案B：FRAME（まだ無い。あれば label・aliases で位置を決める）
    explain: "claude_explain.Conversation | None" = None  # Claude に聞く（読み取りだけ）の会話。メモリの中だけ
    # 人が教えた意味（この画面の会話の中だけ。ファイル・records/・作業ログに書かない。/api/hear の流れは読まない）
    talk: "talk.TalkMemory | None" = None
    # Claude の言い添えに送る、前の会話の文（人・アプリ・Claude。この画面のメモリの中だけ。claude_lead.HISTORY 個まで）
    lead_history: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # 閉じた印（Q-7：新しいジョブを読み込んだら、ほかの画面はすべて閉じる）。立てたら戻さない。理由は "replaced"
    closed: bool = False
    closed_reason: str | None = None
    # P0 H02・H16：候補の世代（候補ができた・変わった・捨てた・無効にしたたびに1つ進める）。承認・出力のボタンは
    # 画面が見ていた世代を送り、今の世代と違えば断る。approval_gen は承認したときの世代
    cand_gen: int = 0
    cand_seen: object | None = None
    approval_gen: int | None = None
    # 保留：取消・言い直しかもしれない文で、はっきりしないとき。承認・出力はできない（承認の記録は残すが使えない）
    hold: bool = False
    voided: dict | None = None  # 最後に無効にした記録（理由・要約）。新しい候補ができたら消す
    # 意味理解（本番候補 v1.0・nl_understand）：聞き返しの途中の会話の枠・直前の候補・Claude に送る直近の発話（メモリの中だけ）
    nl_frame: "nu.Frame | None" = None
    nl_prev: "nu.Previous | None" = None
    nl_hist: list = field(default_factory=list)
    nl_cand_seen: object | None = None
    nl_seed: object | None = None  # 今までの道が聞き返した発話の読み（次の発話で枠の元にする。プログラムが読んだ事実だけ）
    # N-1：この承認の世代で出力した（同じ承認でもう一度出力しない）
    exported_gen: int | None = None

    # Session に渡すシミュレーション（実機用の検査に断られたときだけ呼ばれる）
    def simulate(self, job: jbi.Job, p: ut.ProposedChange, reason: str) -> jbi.ChangeProposal | None:
        self.sim_error = None
        if p.kind != "shift" or p.axis is None:
            self.sim_error = "シミュレーションでも、位置の移動のほかは候補を作りません"
            return None
        try:
            sp = sim.simulate_shift(job, p.position, p.axis, p.delta_mm)
            new_job = jbi.parse_bytes(sp.new_bytes)
        except (sim.SimError, jbi.JbiError) as e:
            self.sim_error = str(e)
            return None
        users = tuple(f"{i + 1}行目「{t}」（この位置を使う命令）" for i, t in sp.users)
        warnings = tuple(w for w in sp.warnings if not w.startswith("実機用の検査では止まります"))
        return jbi.ChangeProposal(
            kind="shift", target=f"{p.position} {p.axis}", line_index=sp.line_index,
            before_line=sp.before_line, after_line=sp.after_line, requested=sp.requested,
            before_value=sp.before_value, after_value=sp.after_value,
            affected=users or ("（この位置を使う命令はありません）",), warnings=warnings, new_job=new_job)


class WebFlow:
    def __init__(self, max_sessions: int = MAX_SESSIONS, interpreter=None,
                 updown_config: "str | Path | None | object" = USE_DEFAULT, robot: str = ud.DEFAULT_ROBOT,
                 explainer: "claude_explain.Explainer | None" = None):
        # Claude に聞く係（読み取りだけ。/api/ask）。答えでジョブ・候補・承認は変えない
        self.explainer = explainer if explainer is not None else claude_explain.Explainer.from_env()
        # 解釈の係：本物の Claude（ANTHROPIC_API_KEY があるとき）か、偽のClaude（モック）
        self.interpreter = interpreter if interpreter is not None else claude_interpreter.from_env()
        self.max_sessions = max_sessions
        # 上下の向きの設定ファイルの場所（None なら設定なし＝未確認）と、アプリで使うロボットの名前（照合で比べるだけ）
        self.updown_config = DEFAULT_UPDOWN_CONFIG if updown_config is USE_DEFAULT else updown_config
        self.robot = robot
        # Claude の言い添え（指示の道の答えの前の一言）。既定は使わない。起動（本物の Claude のとき）で True にする
        self.claude_lead = False
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._closed: OrderedDict[str, str] = OrderedDict()  # 閉じた画面の番号 → 理由（断る文を選ぶだけ）
        self._lock = threading.Lock()

    # ---- 読込 ----
    def load(self, data: bytes, previous: object = None) -> dict:
        """読み込む。previous（前に見ていた画面の番号）があり、中身（//NAME と ///DATE を除いた指紋）が同じなら、
        人が教えた意味を引き継ぐ（E2-1）。違えば引き継がない（E2-2）。ファイル名では決めない。

        有効な画面は1つだけ（唐川さんの決定 Q-7）：previous の有無に関係なく、今あるすべての画面を最初に閉じる。
        新しいジョブが読めなくても閉じる（Q-5）。閉じた画面への要求は、どの API でも状態を変えずに断る。"""
        with self._lock:  # 先に self._lock で外して印を立てる。各画面の錠は、放してから取る（_run と同じ順番）
            closing = self._close_all_locked()
        prev = next((old for old_sid, old in closing if old_sid == previous), None) if isinstance(previous, str) else None
        meanings = None
        if prev is not None:
            with prev.lock:  # 人が教えた意味は、捨てる前に写す（E2-1）
                if prev.talk is not None:
                    meanings = (prev.talk.content_hash, list(prev.talk.meanings))
        for _, old in closing:
            self._discard_closed(old)
        closed_note = {"session_closed": True} if closing else {}
        view = api.job_view(data)
        if not view.get("ok"):
            return {"ok": False, "error": view.get("error") or "ジョブを読めません", **closed_note}
        job = jbi.parse_bytes(data)
        if not job.name:
            return {"ok": False, "error": "ジョブ名の行（//NAME）が読めないため、読み込みません", **closed_note}
        entry = _Entry(session=None, loaded_sha256=hashlib.sha256(data).hexdigest())  # type: ignore[arg-type]
        entry.session = Session(job, simulate=entry.simulate,
                                updown_check=ud.checker(self.updown_config, self.robot))
        entry.session.log.append((self._who(), self.interpreter.notice))
        entry.talk = talk.TalkMemory(content_hash=job.normalized_hash())
        if meanings is not None and meanings[0] == entry.talk.content_hash:
            entry.talk.meanings = meanings[1]
            entry.talk.recheck(job)  # 同じ中身でも、対象の区間が見つかるかを確かめ直す
        sid = secrets.token_urlsafe(16)
        with self._lock:
            late = self._close_all_locked()  # 同じ時間にほかの読み込みが入れた画面も閉じる（有効な画面は1つだけ）
            self._entries[sid] = entry
            while len(self._entries) > self.max_sessions:
                self._entries.popitem(last=False)
        for _, old in late:
            self._discard_closed(old)
        return {"ok": True, "session_id": sid, "view": view, "state": self._state(sid, entry)}

    def state(self, sid: object) -> dict:
        return self._run(sid, lambda e: {"ok": True})

    def candidate_gen_of(self, sid: object) -> int | None:
        """今の候補の世代（読むだけ。状態の candidate_gen と同じ）。閉じた・無い画面は None。"""
        e = self._live(sid)
        if e is None:
            return None
        with e.lock:
            if e.closed:
                return None
            self._touch_gen(e)
            return e.cand_gen

    # ---- 指示（音声・文字）。ここからは承認・出力を呼ばない ----
    def hear(self, sid: object, text: object, source: object = "text") -> dict:
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "指示の文字がありません"}
        if len(text) > MAX_TEXT:
            return {"ok": False, "error": f"指示の文字が長すぎます（{MAX_TEXT}文字まで）"}
        if source not in SOURCES:
            return {"ok": False, "error": "指示の種類（voice か text）が分かりません"}

        def run(e: _Entry) -> dict:
            pre = self._stale_check(e, text, source, remake=True)
            if pre is not None:
                summary, tail = pre.pop("summary", None), pre.pop("tail", "")
                if pre.pop("explain", None) is not None:  # /api/hear からの取消＋説明：取り消すだけ（Claude は呼ばない）
                    pre["message"] = M_CXEX_NOASK.format(summary=summary) + tail
                return pre
            if e.session.repeat_range is not None and rc.is_range_confirm_word(text):
                # 範囲の確認の最中の「この範囲を繰り返す」「はい」など：確定せず、範囲の確認を残して案内する（QA P1-A）。
                # 承認・候補・会話の状態は変えない（候補が無い間だけ範囲の確認がある）
                r = e.session.hear(text, source)
                return {"ok": True, "heard": text, "source": source, "facts": None, "tool_calls": [],
                        "dialog": False, **self._reply(e, r)}
            if ut.read_facing_choice(text) is not None:
                # d：向きの確かめのボタン（唐川さんの決定 11/5）。番号が今の確かめと合うときだけ。［合っている］なら、
                # 確かめた発話をふつうの指示として続けて読む（今までの検査を全部通す）。声・文字では確かめにならない
                kind, x = e.session.facing_choose(text, source)
                label = f"［{ut.read_facing_choice(text)[0]}］"
                if kind != "yes":
                    return {"ok": True, "heard": label, "source": source, "facts": None, "tool_calls": [],
                            "dialog": False, "no_change": True, **self._reply(e, x)}
                cancelled = self._begin_instruction(e)
                out = self._hear_body(e, x, source)
                out["heard"] = label
                return self._say_cancelled(e, out) if cancelled else out
            if ut.is_explain_choice(text):
                # ［説明です］（「〜します」の2択。CHECKER_026 M3）：何もしない。承認・候補・会話の状態も変えない
                r = e.session.explain_only(text, source)
                return {"ok": True, "heard": text, "source": source, "facts": None, "tool_calls": [],
                        "dialog": False, "no_change": True, **self._reply(e, r)}
            cancelled = self._begin_instruction(e)
            out = self._hear_body(e, text, source)
            # 新しい指示（どんな発話でも）で承認を実際に取り消したときは、返事の頭に決まった文（唐川さんの決定②）
            return self._say_cancelled(e, out) if cancelled else out
        return self._run(sid, run)

    def _hear_body(self, e: _Entry, text: str, source: str) -> dict:
        """指示の本体（会話の続き・会話の始め・ふつうの指示）。承認の取り消しの文は呼んだ側（hear）が付ける。"""
        f0 = ut.read_utterance(text)
        talking = e.dialog.step is not None
        if rc.is_repeat_request(text):
            # 回数の変更（docs/REPEAT_COUNT_DESIGN.md）：会話にしない。会話の途中なら会話をやめて、ふつうの指示として読む
            if talking:
                e.dialog.end()
            out = None
        else:
            out = (self._dialog_turn(e, text, source, f0) if talking
                   else self._maybe_start_dialog(e, text, source, f0))
        if out is not None:
            return out
        out = self._instruction(e, text, source)
        s = e.session
        if out.get("candidate_made") or (s.facts is not None and s.facts.op == "undo" and out.get("status") == "ok"):
            e.dialog.estimated = None  # ふつうの指示が通った：推定（未確定）の名前は使わない（断られた発話では消さない）
        if talking:  # 会話の途中の新しい指示：会話をやめたことを知らせる
            out["message"] = dg.ENDED_NEW + (out.get("message") or "")
            out["dialog"] = True
            e.dialog.say(APP_SPEAKER, out["message"], out.get("status") or "ok")
        return out

    def _instruction(self, e: _Entry, text: str, source: str) -> dict:
        """ふつうの指示：Session.hear → 解釈の係 → select / propose / compare / undo。"""
        s = e.session
        r = s.hear(text, source=source)
        calls: list[dict] = []
        if r.candidate_made:
            self._remember(e, r)  # 回数の変更：プログラムが候補まで作った。解釈の係（Claude）は呼ばない
        elif r.status == "ok":
            f = s.facts
            if f is not None and f.op == "undo":
                # Only whole-utterance cancellation, not a vague mention of it.
                # An AI omission cannot leave a cancelled proposal approvable.
                cancel_text = f.raw
                if (s.candidate is not None and s.candidate.kind == "shift" and f.position
                        and s.candidate.target.split()[0] == f.position):
                    # Existing question-choice buttons pin the current position.
                    # Do not drop a reference to another candidate/position.
                    cancel_text = re.sub(r"^C[0-9]{5}を", "", ut.normalize(cancel_text))
                if ut.is_explicit_cancellation(cancel_text) and not f.question:
                    r = s.undo()
                else:
                    r = Reply("ask", "今の変更候補を取り消しますか？ 取り消す場合は「取り消して」と言ってください", facts=f)
                    s.log.append(("聞き返し", r.message))
            elif f is not None and f.op in ("move", "speed") and s.candidate is not None:
                # 承認待ちの候補がある：解釈の係（Claude）には送らない（呼ぶ前に断る）
                r = s.pending_reply(f)
            elif f is not None and f.op in ("move", "speed") and (f.position or s.selected) is None:
                # 位置が決まらなければ、解釈の係（Claude）には送らない
                s.log.append(("聞き返し", NO_POSITION))
                r = Reply("ask", NO_POSITION, facts=f)
            else:
                try:
                    calls = self.interpreter.decide(s.facts, s.selected, list(s.original.positions))
                except claude_interpreter.InterpreterError as ex:
                    # モックへ自動で切り替えない。固定の文で止める
                    s.log.append(("止めた理由", str(ex)))
                    r = Reply("stop", str(ex), facts=f)
                else:
                    r = self._dispatch(e, r, calls)
        return {"ok": True, "heard": text, "source": source, "facts": _facts(s.facts),
                "tool_calls": calls, "dialog": False, **self._reply(e, r)}

    # ---- Claude との会話（voice_teaching/dialog.py） ----
    def _speaker(self) -> str:
        return "Claude" if self.interpreter.kind == "claude" else dg.MOCK_SPEAKER

    def _dialog_out(self, e: _Entry, text: str, source: str, f0, status: str, message: str,
                    who: str | None = None, choices: tuple = ()) -> dict:
        """会話の1回分の応答（ここでは候補を作らない）。会話の欄と作業ログに残す。

        話し手の表示は正直に：プログラムの決まった文は「アプリ」。Claude（モック）の位置の候補を示す文だけを
        who=self._speaker() で出す（_locate の最後だけ）。
        """
        s = e.session
        s.forget_question()
        e.dialog.say(who or APP_SPEAKER, message, status)
        s.log.append(({"ok": "結果", "ask": "聞き返し", "stop": "止めた理由"}[status], message))
        return {"ok": True, "heard": text, "source": source, "facts": _facts(f0), "tool_calls": [], "dialog": True,
                "status": status, "message": message, "blocked_reason": None, "candidate_made": False,
                "choices": [{"label": label, "text": t} for label, t in choices]}

    def _human(self, e: _Entry, text: str, source: str) -> None:
        e.dialog.say("人", text, "human")
        e.session.log.append(("発話", f"{'音声' if source == 'voice' else '文字'}：{text}"))

    def _maybe_start_dialog(self, e: _Entry, text: str, source: str, f0) -> dict | None:
        """場所の言葉（「置くところ」）で始まる会話。ほかの pre_check の結果（打ち消し・繰り返し・疑問の形など）は変えない。"""
        s, d = e.session, e.dialog
        word = dg.find_place_word(text, f0)
        if word is None:
            return None
        ops = [k for k in f0.kinds if k in ut.OPS]
        if (f0.negations or f0.undo_negations or getattr(f0, "repeat", None) or getattr(f0, "question", False)
                or getattr(f0, "shimasu_text", None) is not None
                or getattr(f0, "place_as_destination", False)
                or f0.positions or f0.bad_positions or len(ops) > 1 or f0.op in ("speed", "compare", "undo")
                or any(r != ut.FRONT_BACK_MESSAGE for r in f0.stop_reasons)):
            return None  # 今の流れのまま（打ち消し・繰り返し・疑問の形・「〜します」・行き先の場所の言葉・承認の言葉 など）
        rel = dg.find_relative_dir(text, f0)
        if rel and (dg.has_neighbour_relative(text, word) or f0.axes or f0.vertical or dg.has_sign_word(text)):
            # 向きの言葉と、軸・上げ下げ・符号が一緒（「置くところを奥へ3mm下げて」）、または「置くところの手前」：
            # 向きの言葉を捨てて読まない。会話にせず、今の流れ（止める・聞き返す）のまま
            return None
        two = dg.count_place_words(text) >= 2
        fs = None
        refused = None
        if not two:
            sub = dg.substitute_opening(text, word)
            fs = ut.read_utterance(sub)
            if not fs.kinds and not fs.unknown_words and not fs.unclear_amounts:
                # 「置くところを3mm」「置くところ」：動かす指示として読む（方向・量が無いのは、会話が受け持つ理由）
                fs = ut.read_utterance(sub + "移動")
            if fs.place_ref:
                two = True
            elif not dg.handled_by_dialogue(fs, s.updown):
                # 会話が受け持たない理由（D1 の2つの読み方・知らない言葉・符号 など）：その答えをそのまま返す。
                # 選択肢の文には場所の言葉が無く、別の位置に当たるおそれがあるので、選択肢は出さない
                refused = ut.pre_check(fs, None, s.updown)
        est = d.estimated
        d.start(word, rel, dg.Slots())
        d.estimated = est
        self._human(e, text, source)
        if two:  # 場所の言葉が2つ：1つずつ（候補は頼まない）
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "ask", dg.TWO_PLACES)
        if refused is not None:
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, refused.status, refused.message)
        if s.candidate is not None:  # 承認待ちの候補がある：位置の候補も頼まない
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "stop", s.pending_message, who="アプリ")
        fs = ut.read_utterance(dg.substitute_opening(text, word))
        if fs.unknown_words:  # B1：知らない言葉を捨てて読まない
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "ask", ut.unknown_words_message(fs.unknown_words, "move", s.updown),
                                    who="アプリ")
        slots = dg.read_slots(fs, opening=True, updown=s.updown)
        if slots.problem:
            d.end(clear_estimate=False)
            return self._dialog_out(e, text, source, f0, "stop" if slots.stop else "ask", slots.problem)
        d.estimated = None  # 新しい会話を始めた：前の推定は使わない
        d.amount, d.axis, d.sign = slots.amount, slots.axis, slots.sign
        d.vertical = slots.vertical
        if rel:  # 向きの言葉だけ：方向は必ず6つのボタンで聞く（始めの発話からは決めない）
            d.axis = d.sign = None
            d.vertical = False
        d.turn += 1
        return self._locate(e, text, source, f0, word)

    def _locate(self, e: _Entry, text: str, source: str, f0, word: str) -> dict:
        s, d = e.session, e.dialog
        job = s.original
        frame_pos = dg.frame_lookup(e.frame, word)
        cand = dg.frame_candidate(job, frame_pos) if frame_pos else None
        if cand is not None:  # 案B：専門家が確定した名前。Claude は呼ばない
            d.candidates = [cand]
        elif not sim._is_synthetic(s.original_bytes):  # 合成でないジョブの中身は送らない
            d.step = "pick"
            return self._dialog_out(e, text, source, f0, "ask", dg.NOT_SYNTHETIC.format(word=word), who="アプリ")
        else:
            locate = getattr(self.interpreter, "locate", None)
            if locate is None:
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.NO_LOCATOR, who="アプリ")
            try:
                raw = locate(text, word, list(job.positions), dg.instruction_lines(job))
            except claude_interpreter.InterpreterError as ex:
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.LOCATE_ERROR.format(error=ex), who="アプリ")
            except Exception:
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.LOCATE_FAILED.format(word=word), who="アプリ")
            d.candidates = dg.verify_candidates(job, raw)
            if not d.candidates:  # 照合に通らない答えは使わない（推測で補わない）
                s.log.append((self._who(), "位置の候補は、照合に通りませんでした"))
                d.step = "pick"
                return self._dialog_out(e, text, source, f0, "ask", dg.LOCATE_FAILED.format(word=word), who="アプリ")
            s.log.append((self._who(), "位置の候補：" + "・".join(
                f"{c.position}（根拠 {'・'.join(str(x.line_no) for x in c.evidence)}行目）" for c in d.candidates)))
        d.step = "confirm_position"
        msg = (dg.ASK_ONE.format(word=word, pos=d.candidates[0].position) if len(d.candidates) == 1
               else dg.ASK_MANY.format(word=word, n=len(d.candidates)))
        # Claude（モック）の位置の候補を示す文だけは、その話し手として出す。FRAME で決まったものは「アプリ」
        who = APP_SPEAKER if d.candidates[0].confirmed_by_frame else self._speaker()
        out = self._dialog_out(e, text, source, f0, "ask", msg, who=who)
        d.proposal_mark = len(d.turns)  # 声の肯定は、この直後だけ使える
        return out

    def _dialog_turn(self, e: _Entry, text: str, source: str, f0) -> dict | None:
        """会話の途中の発話。None を返したら、会話をやめて、ふつうの指示として読む。"""
        d = e.dialog
        if d.step == "pick":
            # 手で選ぶ段：位置番号だけの文なら、選んだことにする。それ以外は段を変えずに聞く（集めた量・方向は捨てない）
            name = ut.normalize_position(text.strip().strip("。、.!！"))
            self._human(e, text, source)
            if name is not None:
                r = e.session.select(name)
                if r.status != "ok":
                    return self._dialog_out(e, text, source, f0, "stop", r.message)
                return self._dialog_pick(e)
            v = ut.pre_check(f0, None, e.session.updown)
            if v.status == "stop":
                return self._dialog_out(e, text, source, f0, "stop", v.message)
            return self._dialog_out(e, text, source, f0, "ask", dg.PICK_PROMPT)
        v = ut.pre_check(f0, None, e.session.updown)
        if d.step == "confirm_position":
            fresh = len(d.turns) == d.proposal_mark  # 最後のやり取りが位置の候補の問いか
            self._human(e, text, source)
            if dg.is_affirmative(text):  # 選択であって承認ではない（候補が1つ・問いの直後のときだけ）
                if not fresh:
                    return self._dialog_out(e, text, source, f0, "ask", dg.AFFIRM_BUTTON)
                if len(d.candidates) == 1:
                    return self._confirm_position(e, text, source, f0, d.candidates[0].position)
                return self._dialog_out(e, text, source, f0, "ask", dg.AFFIRM_MANY)
            if f0.op == "undo" and v.status == "ok":
                d.end()
                return self._dialog_out(e, text, source, f0, "ok", dg.END_UNDO)
            if v.status == "stop":  # 打ち消し・承認の言葉など：止めて、同じ所にいる（選択肢もそのまま）
                if (f0.negations or f0.undo_negations) and any(c["id"] == "other" for c in d.choices()):
                    # 打ち消し（「やっぱり違う」）：「ほかの位置」のボタンが見えているので、次にすることを案内する（CHECKER_026 S-pick）
                    return self._dialog_out(e, text, source, f0, "stop", dg.PICK_AFTER_NEGATION, who="アプリ")
                return self._dialog_out(e, text, source, f0, "stop", v.message, who="アプリ")
            return self._dialog_out(e, text, source, f0, "ask", dg.CONFIRM_STAY)
        # ask_move：方向と量を聞いている
        if f0.negations or f0.undo_negations:
            self._human(e, text, source)
            return self._dialog_out(e, text, source, f0, "stop", v.message, who="アプリ")
        if f0.op == "undo" and v.status == "ok":
            self._human(e, text, source)
            d.end()
            return self._dialog_out(e, text, source, f0, "ok", dg.END_UNDO)
        if f0.positions or f0.bad_positions or f0.op in ("speed", "compare") or "select" in f0.kinds:
            d.end()  # 新しい指示：会話をやめて、ふつうに読む（hear が「会話をやめて…」と知らせる）
            return None
        self._human(e, text, source)
        if d.confirm_go:  # 手で選んだ位置の確かめ：［go］のボタンのほかでは進めない
            return self._dialog_out(e, text, source, f0, v.status if v.status == "stop" else "ask",
                                    v.message if v.status == "stop" else dg.GO_BUTTON)
        if getattr(f0, "shimasu_text", None) is not None and not f0.question:
            # 「〜します」：ふつうの指示と同じく、選んでいる位置で外した文を確かめる（検査・上限まで通るときだけ2択）。
            # ［変更したい］の文には、その位置番号を入れる（あとで別の位置を選んでも、その位置に当てない。P2-1）
            v = e.session.check_shimasu(f0, ut.pre_check(f0, e.session.selected, e.session.updown))
        if getattr(f0, "repeat", None) or getattr(f0, "question", False) or getattr(f0, "shimasu_text", None) is not None:
            # 繰り返し・疑問の形・「〜します」は、今の pre_check の答えのまま（量として使わない）
            return self._dialog_out(e, text, source, f0, v.status if v.status != "ok" else "ask",
                                    v.message or d.ask_move_message(), who="アプリ", choices=v.choices)
        others = [r for r in f0.stop_reasons if r != ut.FRONT_BACK_MESSAGE]
        if others:  # 承認・起動の言葉など
            return self._dialog_out(e, text, source, f0, "stop", "。".join(others), who="アプリ")
        rel = dg.find_relative_dir(text, f0)
        if rel:  # 手前・右などは軸にしない。6つのボタンで聞く
            d.relative = rel
            return self._dialog_out(e, text, source, f0, "ask", dg.RELATIVE_ASK.format(rel=rel))
        if f0.unknown_words:  # B1
            return self._dialog_out(e, text, source, f0, "ask", ut.unknown_words_message(f0.unknown_words, "move", e.session.updown),
                                    who="アプリ")
        if f0.place_ref:  # 答えの中の場所の言葉：会話をやめて、今の流れ（聞き返し）のまま
            d.end()
            return None
        if f0.kinds and not dg.handled_by_dialogue(f0, e.session.updown):
            # 会話が受け持たない理由（2つの操作・D1 の2つの読み方・上下の向きが未確認 など）：今の pre_check の答えをそのまま返す
            v = ut.pre_check(f0, None, e.session.updown)
            # 選択肢（D1 など）の文には、選んでいる位置を入れる（あとで別の位置を選んでも、その位置に当てない）
            return self._dialog_out(e, text, source, f0, v.status, v.message, choices=e.session.pin_choices(v.choices))
        slots = dg.read_slots(f0, updown=e.session.updown)
        if slots.problem:
            return self._dialog_out(e, text, source, f0, "stop" if slots.stop else "ask", slots.problem)
        if slots.amount is None and slots.axis is None and slots.sign is None:
            return self._dialog_out(e, text, source, f0, "ask", dg.NOTHING_READ + d.ask_move_message())
        return self._merge_slots(e, text, source, f0, slots.amount, slots.axis, slots.sign, slots.vertical)

    def _merge_slots(self, e: _Entry, text, source, f0, amount, axis, sign, vertical: bool = False) -> dict:
        d = e.dialog
        d.confirm_go = False
        if amount is not None:
            d.amount = amount
        problem = None
        if sign is not None and d.sign is not None and sign != d.sign:
            problem = dg.SIGN_CONFLICT
        elif axis is not None and d.axis is not None and axis != d.axis:
            problem = dg.AXIS_CONFLICT
        if problem:  # 前の答えと食い違う：どちらも使わず、ボタンで選び直してもらう
            d.axis = d.sign = None
            d.vertical = False
            d.turn += 1
            return self._dialog_out(e, text, source, f0, "ask", problem)
        if axis is not None:
            # 軸を「上に・下げて」から読んだか（軸の文字・方向のボタンなら False）。前の答えと同じ軸なら、どちらかの印を残す
            d.vertical = vertical or (d.vertical and d.axis == axis)
        d.axis = axis if axis is not None else d.axis
        d.sign = sign if sign is not None else d.sign
        if d.complete():
            return self._run_canonical(e, source)
        d.turn += 1
        return self._dialog_out(e, text, source, f0, "ask", d.ask_move_message())

    def _confirm_position(self, e: _Entry, text: str, source: str, f0, position: str) -> dict:
        """人が選んだ位置（ボタンか短い肯定）。選んだだけで、ジョブ・承認は変えない。"""
        s, d = e.session, e.dialog
        cand = next((c for c in d.candidates if c.position == position), None)
        r = s.select(position)
        if r.status != "ok":
            return self._dialog_out(e, text, source, f0, "stop", r.message, who="アプリ")
        d.position = position
        d.estimated = (None if cand is None or cand.confirmed_by_frame else
                       {"position": position, "word": d.word, "label": dg.ESTIMATE_LABEL, "by": self._speaker()})
        d.candidates = []
        d.step = "ask_move"
        if d.complete():
            return self._run_canonical(e, source)
        d.turn += 1
        return self._dialog_out(e, text, source, f0, "ask", d.ask_move_message())

    def _run_canonical(self, e: _Entry, source: str) -> dict:
        """集めたことを決まった文にして、ふつうの指示と同じ流れに通す（ガードはすべてそのまま）。"""
        d = e.dialog
        text = dg.canonical_text(d.position, d.axis, d.sign, d.amount, vertical=d.vertical)
        est = d.estimated
        d.end()
        out = self._instruction(e, text, source)
        if out.get("candidate_made"):
            d.estimated = est  # 会話で選んだ位置の候補にだけ「推定（未確定）」を残す（この画面の中だけ）
            d.say(APP_SPEAKER, dg.DONE, "ok")  # 決まった文（プログラム）
        else:
            d.say("アプリ", out.get("message") or "", out.get("status") or "stop")
        out["dialog"] = True
        return out

    def choose(self, sid: object, choice: object, turn: object) -> dict:
        """会話の選択肢のボタン。今の選択肢の番号（turn）と id が合うときだけ使う。承認・出力は呼ばない。"""
        def run(e: _Entry) -> dict:
            d = e.dialog
            ids = {c["id"]: c["label"] for c in d.choices()}
            if (d.step is None or isinstance(turn, bool) or not isinstance(turn, int) or turn != d.turn
                    or not isinstance(choice, str) or choice not in ids):
                e.session.forget_range()  # ほかのボタンを押した：回数の変更の範囲の確認も捨てる（止める側）
                e.session.log.append(("止めた理由", dg.STALE))
                return {"ok": True, "status": "stop", "message": dg.STALE, "blocked_reason": None,
                        "candidate_made": False, "choices": [], "dialog": True, "tool_calls": []}
            cancelled = self._begin_instruction(e)
            label = ids[choice]
            d.say("人", label, "human")
            e.session.log.append(("選択", f"ボタン：{label}"))
            if choice.startswith("pos:"):
                out = self._confirm_position(e, label, "text", None, choice[4:])
            elif choice == "go":  # 手で選んだ位置で、方向と量がそろったことの確かめ
                out = self._run_canonical(e, "text")
            elif choice == "other":
                d.step, d.candidates = "pick", []
                d.turn += 1
                out = self._dialog_out(e, label, "text", None, "ask", dg.PICK_PROMPT, who="アプリ")
            else:
                direction = choice[4:]
                out = self._merge_slots(e, label, "text", None, None, direction[0], 1 if direction[1] == "+" else -1)
            return self._say_cancelled(e, out) if cancelled else out
        return self._run(sid, run)

    def repeat_choose(self, sid: object, choice: object, turn: object) -> dict:
        """回数の変更の範囲の確認のボタン（［この範囲を繰り返す］［違う］）。画面のボタンからだけ呼ぶ。
        今の範囲の確認の番号（turn）と合わなければ使わない。承認・出力は呼ばない（承認は今どおり確認表示のボタン）。"""
        def run(e: _Entry) -> dict:
            r = e.session.confirm_repeat(turn, choice)
            if r.candidate_made:
                self._remember(e, r)
            return {"ok": True, "tool_calls": [], **self._reply(e, r)}
        return self._run(sid, run)

    def _dispatch(self, e: _Entry, heard: Reply, calls: list[dict]) -> Reply:
        s = e.session
        who = self._who()
        if not calls:
            f = s.facts
            s.log.append((who, "道具は呼びませんでした"))
            if f is not None and f.op in ("move", "speed"):
                s.log.append(("聞き返し", NOT_DECIDED))
                return Reply("ask", NOT_DECIDED, facts=f)
            return heard
        r = heard
        for c in calls:
            tool, args = c["tool"], c["args"]
            s.log.append((who, f"道具 {tool} を呼びました：{_args_text(args)}"))
            if tool == "ask_user":
                q = str(args.get("question") or NOT_DECIDED)
                s.log.append(("聞き返し", q))
                r = Reply("ask", q, facts=s.facts)
            elif tool == "select":
                r = s.select(args["position"])
            elif tool == "propose_change":
                e.sim_error = None
                r = s.propose(args)
                if r.status != "ok" and r.blocked_reason and e.sim_error:
                    s.log.append(("止めた理由", f"シミュレーションの候補も作りません：{e.sim_error}"))
                    # K4（11/6）：同じ上限の理由を2回言わない。理由は1回・次にすることを足す（判定は今のまま）
                    msg = (f"{r.message}（シミュレーションの候補も作りません）" if "仮の上限" in r.message
                           else f"{r.message}。シミュレーションの候補も作りません：{e.sim_error}")
                    r = Reply(r.status, _limit_next(msg), facts=r.facts, blocked_reason=r.blocked_reason)
                elif r.status != "ok" and r.blocked_reason:
                    r = Reply(r.status, _limit_next(r.message), facts=r.facts, blocked_reason=r.blocked_reason)
            elif tool == "compare":
                r = s.compare()
            elif tool == "undo":
                r = s.undo()
            else:  # 決めた道具のほかは使わない
                r = Reply("stop", "知らない道具の呼び出しのため、使いません")
            self._remember(e, r)
            if r.status != "ok":
                break
        return r

    def _who(self) -> str:
        return "Claude" if self.interpreter.kind == "claude" else "モック"

    # ---- 画面の操作 ----
    def select(self, sid: object, position: object) -> dict:
        def run(e: _Entry) -> dict:
            s = e.session
            name = ut.normalize_position(position) if isinstance(position, str) else None
            if name is None or name not in s.original.positions:
                # 形の違う位置・ジョブに無い位置：何も変えない（承認も取り消さない。選び直しの文も出さない）
                return {"ok": True, **self._reply(e, s.select(position if isinstance(position, str) else ""))}
            cancelled = self._cancel_approval(e, "位置を選び直したため")
            out = self._select_body(e, name)
            # 承認を実際に取り消したときは、候補が残ったか（同じ位置）・捨てたか（別の位置）で文を決める。声の「C00004を選択して」と同じ
            return self._say_cancelled(e, out) if cancelled else out
        return self._run(sid, run)

    def _select_body(self, e: _Entry, position: object) -> dict:
        """位置の選び直しの本体。承認の取り消しの文は呼んだ側（select）が付ける。"""
        before = e.session.selected
        r = e.session.select(position if isinstance(position, str) else "")
        d = e.dialog
        if r.status == "ok":
            fr = e.nl_frame
            if fr is not None and (fr.position is not None and fr.position != e.session.selected):
                e.nl_frame = None  # 聞き返しの途中で別の位置：持っていた方向・量を別の位置に当てない
            if before is not None and e.session.selected != before:
                e.session.forget_facing_confirmation()  # D5（11/6）：右・左の対応は、確かめた位置の中だけで覚える
            d.estimated = None  # 人が選び直した：推定の名前は使わない
            if d.step == "pick":  # 「ほかの位置」のあとの手で選ぶ段：選んだ位置で続ける
                d.say("人", f"{e.session.selected} を選びました", "human")
                return self._dialog_pick(e)
            if d.step is not None:
                # 位置の候補の確かめ・方向と量を聞いている段での選び直し：会話をやめる。持っていた量・方向を
                # 別の位置に当てない（CHECKER_028 A）
                d.say("人", f"{e.session.selected} を選びました", "human")
                d.end()
                d.amount = d.axis = d.sign = None
                d.vertical = False
                r = Reply("ok", dg.ENDED_RESELECT + r.message)
        return {"ok": True, **self._reply(e, r)}

    def _dialog_pick(self, e: _Entry) -> dict:
        """会話の途中で人が位置を選んだ（「ほかの位置」のあとなど）：その位置で、方向と量を聞く。"""
        d = e.dialog
        d.estimated = None
        d.position, d.candidates, d.step = e.session.selected, [], "ask_move"
        d.turn += 1
        if d.complete():
            # 手で選んだだけでは候補にしない。ボタン1つで確かめる（声では進めない）
            d.confirm_go = True
            return self._dialog_out(e, "", "text", None, "ask", d.confirm_go_message())
        return self._dialog_out(e, "", "text", None, "ask", d.ask_move_message())

    def compare(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            r = e.session.compare()
            self._remember(e, r)
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    def undo(self, sid: object) -> dict:
        def run(e: _Entry) -> dict:
            was_approved = self._approved(e)
            self._cancel_approval(e, "元に戻したため")
            if e.dialog.step is not None:
                e.dialog.end()
            e.dialog.estimated = None
            r = e.session.undo()
            if was_approved and e.session.candidate is None:
                # 承認のあとの「元に戻す」ボタンも、声の「元に戻して」と同じ決まった文（唐川さんの決定・内部 QA N-2）
                r = Reply(r.status, APPROVAL_AND_CANDIDATE_CANCELLED, facts=getattr(r, "facts", None))
            return {"ok": True, **self._reply(e, r)}
        return self._run(sid, run)

    # ---- 承認（画面の承認ボタンからだけ呼ぶ） ----
    def approve(self, sid: object, approver: object, *, candidate_gen: object) -> dict:
        """candidate_gen：画面が見ていた候補の世代（状態の candidate_gen）。省略できない。今の世代と違えば断る。"""
        def run(e: _Entry) -> dict:
            s = e.session
            self._touch_gen(e)
            ch = s.candidate
            if ch is None:
                return self._fail(e, "承認する変更候補がありません" + self._voided_note(e))
            if isinstance(candidate_gen, bool) or not isinstance(candidate_gen, int):
                return self._fail(e, M_NOGEN)
            if candidate_gen != e.cand_gen:
                return self._fail(e, M_STALEGEN)
            if e.hold:
                return self._fail(e, M_HOLD)
            if not isinstance(approver, str) or not approver.strip():
                return self._fail(e, "承認した人の名前がないため、承認できません")
            if ch.kind == "repeat":
                return self._approve_repeat(e, ch, approver)
            if ch.kind != "shift":
                return self._fail(e, "シミュレーションの出力は位置の移動と回数の変更だけです。この候補は承認できません")
            if s.candidate_blocked_reason:
                # 実機用の検査で止まった候補は、合成ジョブでも承認しない（CHECKER_019 R1）
                return self._fail(e, f"実機用の検査で止まった変更候補は、承認できません（理由：{s.candidate_blocked_reason}）")
            position, axis = ch.target.split()
            try:
                sp = sim.simulate_shift(s.original, position, axis, ch.requested)
            except sim.SimError as ex:
                return self._fail(e, f"承認できません：{ex}")
            if sp.original_bytes != s.original_bytes or sp.new_bytes != ch.new_job.render_bytes():
                return self._fail(e, "変更候補と、出力する中身が合わないため、承認できません")
            st = s.updown_status()  # 設定を読み直す。候補を作ったときと違えば断る
            if s.candidate_updown is None or s.candidate_updown.token != st.token:
                return self._fail(e, UPDOWN_CHANGED_APPROVE)
            try:
                ap = sim.approve(sp, approver, updown=st)
            except sim.SimError as ex:
                return self._fail(e, str(ex))
            e.sim_proposal, e.approval, e.approved_candidate = sp, ap, ch
            e.approval_updown = st
            e.approval_gen = e.cand_gen
            e.updown_cancelled = False
            e.exported = None
            s.log.append(("承認", f"{ap.approved_by} さんが承認ボタンで承認しました（{ap.approved_at}）。{_updown_note(st)}"))
            return {"ok": True, "state": None}
        return self._run(sid, run)

    def _approve_repeat(self, e: _Entry, ch, approver: str) -> dict:
        """回数の変更の承認（ボタンから）。元のジョブから同じ候補を作り直し、バイト列が同じときだけ承認する。"""
        s = e.session
        try:
            sp = sim.simulate_repeat(s.original, ch.start, ch.end, ch.after_count)
        except sim.SimError as ex:
            return self._fail(e, f"承認できません：{ex}")
        if sp.original_bytes != s.original_bytes or sp.new_bytes != ch.new_job.render_bytes():
            return self._fail(e, "変更候補と、出力する中身が合わないため、承認できません")
        st = s.updown_status()  # 位置の移動と同じく、候補を作ったときの照合の結果と比べる（ガードを変えない）
        if s.candidate_updown is None or s.candidate_updown.token != st.token:
            return self._fail(e, UPDOWN_CHANGED_APPROVE)
        try:
            ap = sim.approve(sp, approver, updown=st)
        except sim.SimError as ex:
            return self._fail(e, str(ex))
        e.sim_proposal, e.approval, e.approved_candidate = sp, ap, ch
        e.approval_updown = st
        e.approval_gen = e.cand_gen
        e.updown_cancelled = False
        e.exported = None
        s.log.append(("承認", f"{ap.approved_by} さんが承認ボタンで承認しました（{ap.approved_at}）。{_updown_note(st)}"))
        return {"ok": True, "state": None}

    # ---- シミュレーション用の出力（合成ジョブだけ） ----
    def export(self, sid: object, *, candidate_gen: object) -> dict:
        """candidate_gen：画面が見ていた候補の世代。省略できない。承認したときの世代・今の世代と違えば断る。"""
        def run(e: _Entry) -> dict:
            self._touch_gen(e)
            if not self._approved(e):
                # 上下の向きの設定が変わって承認を消した（間に状態の要求があっても）なら、その理由を出す
                return self._fail(e, UPDOWN_CHANGED_EXPORT if e.updown_cancelled else NOT_APPROVED + self._voided_note(e))
            if isinstance(candidate_gen, bool) or not isinstance(candidate_gen, int):
                return self._fail(e, M_NOGEN)
            if candidate_gen != e.cand_gen or e.approval_gen != e.cand_gen:
                return self._fail(e, M_STALEGEN)
            if e.hold:
                return self._fail(e, M_HOLD)
            if e.exported_gen is not None and e.exported_gen == e.approval_gen and e.exported:
                # N-1（11/7）：同じ承認では1回だけ出力する（押し直しで同じ中身のファイルを増やさない）
                return self._fail(e, M_ALREADY_EXPORTED.format(name=Path(e.exported).name))
            if not self._updown_unchanged(e):  # 設定を読み直す。変わっていれば承認を消して断る
                self._cancel_approval(e, UPDOWN_CHANGED_WHY)
                return self._fail(e, UPDOWN_CHANGED_EXPORT)
            try:
                path = sim.export_sim(e.sim_proposal, e.approval)
            except sim.SimError as ex:
                return self._fail(e, f"出力しませんでした：{ex}")
            full = str(Path(path).resolve())
            e.exported = full
            e.exported_gen = e.approval_gen
            e.session.log.append(("出力", f"シミュレーション用のジョブを出力しました：{full}（実機には入れないでください）。"
                                          f"{_updown_note(e.approval_updown)}"))
            return {"ok": True, "path": full, "file_name": Path(path).name}
        return self._run(sid, run)

    # ---- Claude に聞く（読み取りだけ。docs/CLAUDE_API.md 8章・CHECKER_031 E1〜E7） ----
    # ---- Step F：発話の入口を1つに（docs/STEP_F_UNIFIED_INPUT.md。CHECKER_031 3章・唐川さんの指示 11/3 夜） ----
    def talk(self, sid: object, text: object, source: object = "text") -> dict:
        """1つの入口で受ける（声・文字）。振り分けはまずプログラムが決める：
        - 承認・起動などの言葉・会話の途中・範囲の確認の途中・はっきりした指示（位置・速度・選択・元に戻す など）→ 指示の道（hear）
        - 回数の変更の希望（プログラムが人の文から回数を読めた）→ 会話の道（ask：Claude が答える。読み取りだけ）＋
          変更の道へは、プログラムが作った決まった文（rc.canonical）だけを渡す（範囲の確認のボタン → 候補）
        - それ以外（説明・意味を教える）→ 会話の道（ask）
        Claude の答えの数・座標は使わない。候補を作るのは今までの道（_accept・confirm_repeat）だけ。"""
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "指示の文字がありません"}
        if len(text) > MAX_TEXT:
            return {"ok": False, "error": f"指示の文字が長すぎます（{MAX_TEXT}文字まで）"}
        if source not in SOURCES:
            return {"ok": False, "error": "指示の種類（voice か text）が分かりません"}
        e = self._live(sid)
        if e is None:
            return self._closed_reply(sid, e)
        text = text.strip()
        with e.lock:
            if e.closed:  # 錠を待つ間に読み直しで閉じられた（ここから先も、錠を取るたびに確かめる）
                return self._closed_reply(sid, e)
            # 新しい発話（どの道でも。会話の道・回数の変更の希望も）：前の向きの確かめのボタンは使えない（QA 003 M2）
            e.session.forget_facing()
            pre = self._stale_check(e, text, source, said_by_person=True, remake=True)
            route = "stale" if pre is not None else self._talk_route(e, text)
            if route not in ("hear", "stale"):
                # 指示の道以外（会話・ボタンの案内・変更が2つ・回数）へ回した発話のあとに、前の聞き返しへの答えを当てない
                # （Safety F1・QA M-1。11/5）。捨てるのは未確定の聞き返しだけ（候補・承認・選択は変えない）
                e.session.forget_question()
            nl_mode = self._nl_mode(e, text, route)
            if route not in ("hear", "ask"):
                e.nl_frame = None  # 取消・ボタンの案内・回数などへ回した：聞き返しの途中の枠は使わない
        ask_out = hear_out = None
        via: list[str] = []
        if nl_mode == "understand":
            # 意味理解（Claude）を先に使う。Claude の読みは Python が確かめ、決まった文にして今までの道（hear）に通す
            got = self._nl_turn(sid, e, text, source)
            if got is not None and got.get("session_closed"):
                return got
            if got is not None:
                hear_out, route = got, "nl"
                via.append("hear")
        elif nl_mode == "rescue":
            hear_out = self.hear(sid, text, source)
            if hear_out.get("session_closed"):
                return hear_out
            via.append("hear")
            route = "nl"
            if self._nl_rescuable(e, hear_out):
                got = self._nl_turn(sid, e, text, source, legacy=hear_out)
                if got is not None and got.get("session_closed"):
                    return got
                if got is not None:
                    hear_out = got
        if route == "nl":
            pass
        elif route == "stale":
            # 取消・言い直し・保留（P0 H02・H16）：前の候補は錠の中で、もう無効・保留にしてある。説明の頼みがあれば、
            # 説明の節だけを Claude に聞く（読み取りだけ。取消の節は送らない。候補はもう無い）
            summary, tail = pre.pop("summary", None), pre.pop("tail", "")
            explain = pre.pop("explain", None)
            hear_out = pre
            via.append("hear")
            if explain is not None:
                hear_out["message"] = M_CXEX.format(summary=summary) + tail
                ask_out = self.ask(sid, explain, source=source, via_talk=True)
                if ask_out.get("session_closed"):
                    return ask_out
                via.append("ask")
        elif route in ("button", "approved", "several", "purpose_or_change"):
            f0 = ut.read_utterance(text)
            msg = (STEP_F_APPROVED_NEXT if route == "approved"
                   else STEP_F_ONE_AT_A_TIME if route == "several"
                   else STEP_F_PURPOSE_OR_CHANGE if route == "purpose_or_change"
                   else ("。".join(f0.stop_reasons) if f0.stop_reasons else ut.APPROVE_MESSAGE))
            with e.lock:
                if e.closed:
                    return self._closed_reply(sid, e)
                if msg == STEP_F_APPROVED_NEXT and e.hold:
                    # 保留中（「ちょっと待って」など）：出力できないので出力を勧めない（UI-1・11/6。文だけ。保留・承認・出力の判定は変えない）
                    msg = M_HOLD
                e.session.log.append(("発話", f"{'音声' if source == 'voice' else '文字'}：{text}"))
                e.session.log.append(("止めた理由", msg))
            hear_out = {"ok": True, "status": "stop", "message": msg, "heard": text, "source": source}
            if route in ("button", "approved"):
                # N-2（11/7）：承認・出力はボタンで行う案内・相づち（「はい」）は、エラーの見出しで出さない（状態・判定は変えない）
                hear_out["tone"] = "guide"
            via.append(route if route in ("several", "purpose_or_change") else "button")
        elif route in ("next", "bare_number"):
            with e.lock:
                if e.closed:
                    return self._closed_reply(sid, e)
                msg = self._next_text(e) if route == "next" else M_BARE_NUMBER
                e.session.log.append(("発話", f"{'音声' if source == 'voice' else '文字'}：{text}"))
                e.session.log.append(("案内" if route == "next" else "聞き返し", msg))
            hear_out = {"ok": True, "status": "ok" if route == "next" else "ask", "message": msg, "heard": text,
                        "source": source}
            via.append(route)
        elif route == "hear":
            hear_out = self.hear(sid, text, source)
            if hear_out.get("session_closed"):
                return hear_out
            via.append("hear")
        else:
            ask_out = self.ask(sid, text, source=source, via_talk=True)
            if ask_out.get("session_closed"):
                return ask_out
            via.append("ask")
            if route == "count":
                with e.lock:
                    if e.closed:
                        return self._closed_reply(sid, e)
                    refused = (not ask_out.get("ok")
                               or ask_out.get("status") in ("count_refused", "explain_only")
                               or ask_out.get("intent") != "change_request"
                               or (ask_out.get("change_request") or {}).get("kind") != "repeat"
                               or bool((ask_out.get("change_record") or {}).get("mismatch")))
                    canon = None if (refused or e.session.candidate is not None) else self._repeat_canonical(e, text)
                if canon is not None:
                    hear_out = self.hear(sid, canon, "text")  # プログラムが作った文（人の文は会話の道の記録にある）
                    if hear_out.get("session_closed"):
                        return hear_out
                    via.append("hear")
                    with e.lock:
                        if e.closed:
                            return self._closed_reply(sid, e)
                        started = e.session.repeat_range is not None
                    if started and isinstance(ask_out, dict):
                        # 範囲の確認を始めた：「読み取りだけ」「候補は作りません」の文は、今の状態と合わないので、次にすることの文にする
                        ask_out["notes"] = [STEP_F_RANGE_NOTE]
                        ask_out["change_record"] = None
                    elif isinstance(ask_out, dict) and isinstance(hear_out, dict) and hear_out.get("status") != "ok":
                        # K5（11/6）：プログラムが断った回数：「受け取りました」「範囲のボタン」の文は今の状態と食い違うので出さない
                        ask_out["reply"] = M_COUNT_REFUSED_LEAD
                        ask_out["notes"] = []
                        ask_out["change_record"] = None
        if (self.claude_lead and via == ["hear"] and isinstance(hear_out, dict) and hear_out.get("ok")
                and isinstance(hear_out.get("message"), str) and hear_out["message"]):
            self._add_lead(e, text, hear_out)
        with e.lock:
            if e.closed:
                return self._closed_reply(sid, e)
            self._remember_talk(e, text, ask_out, hear_out)
            self._nl_after(e, text, hear_out)
            state = self._state(sid, e)
        return {"ok": True, "via": via, "ask": ask_out, "hear": hear_out, "state": state, "heard": text, "source": source}

    # ---- 意味理解（本番候補 v1.0・voice_teaching/nl_understand.py） ----
    def _nl_available(self, e: _Entry) -> bool:
        """意味理解を使える状態か（錠の中で呼ぶ）。候補・承認・会話・範囲の確認・回数の確かめの途中は、今までの道だけ。"""
        s = e.session
        return (callable(getattr(self.interpreter, "understand", None)) and s.candidate is None
                and not self._approved(e) and e.dialog.step is None and s.repeat_range is None
                and (e.talk is None or e.talk.pending is None))

    def _nl_mode(self, e: _Entry, text: str, route: str) -> str | None:
        """"understand"（先に Claude）／"rescue"（今までの道のあと、聞き返しなら Claude）／None（今までの道だけ）。錠の中で呼ぶ。"""
        if route not in ("hear", "ask") or not self._nl_available(e):
            e.nl_frame = None if route not in ("hear", "ask") else e.nl_frame
            return None
        if ut.read_facing_choice(text) is not None or ut.is_explain_choice(text):
            return None  # 画面のボタンの文（向きの確かめ・説明です）は今までの道
        facts, v = e.session.dry_check(text)
        # 安全のための止め・聞き返しは、今までの道が決める（Claude に回さない）
        if (facts.negations or facts.undo_negations or facts.stop_reasons or facts.place_ref or facts.repeat
                or rc.is_repeat_request(text) or v.confirm_position is not None
                or (facts.op is not None and facts.op != "move") or ut.is_updown_unset_ask(v.message)):
            if (facts.negations or facts.undo_negations or facts.stop_reasons or facts.op == "undo"
                    or _NEGATION_WHOLE.fullmatch(_bare(text))):
                e.nl_frame = None  # 聞き返しの途中の「やっぱりやめる」「だめ」：持っていた方向・量は次の文に当てない
            return None
        if _NEGATION_WHOLE.fullmatch(_bare(text)):
            e.nl_frame = None
            return None
        if e.nl_frame is not None:
            return "understand"  # 聞き返しの途中：前の枠と合わせて読む
        if route == "ask":
            nt = ut.normalize(text)
            if facts.question or _QUESTION_WORDS.search(nt):
                return None  # 質問：説明の係（読み取りだけ）
            return "understand"
        if v.status == "ok":
            return "rescue"  # 今までの道で読める指示：そのまま（位置が無いときの聞き返しだけ Claude で補う）
        return "understand"

    def _nl_rescuable(self, e: _Entry, out: dict) -> bool:
        """今までの道の答えが、選択肢の無い聞き返しか（上下の向きの案内は除く）。"""
        if not isinstance(out, dict) or out.get("status") != "ask" or out.get("choices") or out.get("candidate_made"):
            return False
        msg = out.get("message") or ""
        with e.lock:
            return (not e.closed and self._nl_available(e) and not ut.is_updown_unset_ask(msg)
                    and e.session._updown_followup is None)

    def _nl_turn(self, sid: object, e: _Entry, text: str, source: str, legacy: dict | None = None) -> dict | None:
        """Claude に読ませ、Python で確かめる。決まった文ができたら今までの道（hear）に通す。
        None：位置の移動ではない（今までの道・説明の係へ）。"""
        with e.lock:
            if e.closed:
                return self._closed_reply(sid, e)
            s = e.session
            positions = list(s.original.positions)
            state = nu.build_state(positions, s.selected, e.nl_prev, e.nl_frame, e.nl_hist)
            frame, selected, prev = e.nl_frame, s.selected, e.nl_prev
            if frame is None and legacy is None and e.nl_seed is not None:
                # 今までの道の聞き返し（「上に動かしたい」→ 向きの確かめ）への答え：前の発話の確かな項目から枠を作る。
                # 方向を軸にするのは、決まった文を通したあとの今までの確かめ（ボタン）だけ
                frame = nu.frame_from_facts(e.nl_seed, selected)
                state = nu.build_state(positions, selected, prev, frame, e.nl_hist)
        try:
            data = self.interpreter.understand(state, text)
        except nu.UnderstandError as ex:
            with e.lock:
                if e.closed:
                    return self._closed_reply(sid, e)
                e.session.log.append(("Claude の読み", f"使えませんでした（{ex}）"))
                if legacy is not None:
                    return None  # 今までの道の聞き返しのまま
                e.nl_frame = None
                msg = f"{ex}。位置番号・方向・量（mm）をはっきり言えば、プログラムだけで進められます（例：C00003 を Zプラス 5ミリ）"
                e.session.log.append(("止めた理由", msg))
            return {"ok": True, "status": "stop", "message": msg, "heard": text, "source": source, "choices": [],
                    "candidate_made": False, "blocked_reason": None, "nl": {"error": ex.kind}}
        with e.lock:
            if e.closed:
                return self._closed_reply(sid, e)
            s = e.session
            if not self._nl_available(e) or s.selected != selected:
                return None  # Claude を待つ間に状態が変わった：今までの道へ（読みは使わない）
            o = nu.interpret(data, text, positions=positions, selected=selected, previous=prev, frame=frame)
            s.log.append(("発話", f"{'音声' if source == 'voice' else '文字'}：{text}") if legacy is None else ("Claude の読み", "聞き返しを補います"))
            s.log.append(("Claude の読み", nu.summary(data, o)))
            if o.kind == "not_move":
                e.nl_frame = None
                return None
            if o.kind == "ask":
                e.nl_frame = o.frame
                s.forget_question()  # 今までの道の聞き返しには合わせない（枠で続ける）
                s.log.append(("聞き返し", o.text))
                return {"ok": True, "status": "ask", "message": o.text, "heard": text, "source": source, "choices": [],
                        "candidate_made": False, "blocked_reason": None, "nl": {"ask": o.frame.missing() if o.frame else []}}
            e.nl_frame = None
            s.forget_question()
            canon = o.text
        out = self.hear(sid, canon, "text")  # プログラムが作った決まった文（今までの検査・ボタン・上限・承認を全部通る）
        if out.get("session_closed"):
            return out
        out["heard"] = text
        out["nl"] = {"canonical": canon}
        return out

    def _nl_after(self, e: _Entry, text: str, hear_out: dict | None) -> None:
        """会話の記録（Claude に送る直近の文）と、直前の候補（「同じだけ」「あと2ミリ」）を残す。錠の中で呼ぶ。"""
        h = e.nl_hist
        h.append({"who": "人", "text": text[:120]})
        if isinstance(hear_out, dict) and hear_out.get("status") == "ask" and isinstance(hear_out.get("message"), str):
            h.append({"who": "アプリ", "text": hear_out["message"][:120]})
        del h[:-nu.MAX_HISTORY]
        e.nl_seed = (e.session.facts if isinstance(hear_out, dict) and hear_out.get("status") == "ask"
                     and "nl" not in hear_out and e.session.candidate is None else None)
        c = e.session.candidate
        if c is not None and c is not e.nl_cand_seen and getattr(c, "kind", None) == "shift":
            e.nl_cand_seen = c
            e.nl_frame = None
            parts = c.target.split()
            if len(parts) == 2:
                e.nl_prev = nu.Previous(parts[0], parts[1], float(c.after_value) - float(c.baseline_value
                                                                                         if c.baseline_value is not None
                                                                                         else c.before_value))

    def _next_text(self, e: _Entry) -> str:
        """K2：次にすることの決まった文（錠の中で呼ぶ）。今の状態（保留・承認・候補・選択）から決める。状態は変えない。"""
        s = e.session
        if s.candidate is not None and e.hold:
            return M_HOLD
        if self._approved(e):
            return STEP_F_APPROVED_NEXT
        if s.candidate is not None:
            return M_NEXT_CANDIDATE.format(summary=_summary(s.candidate))
        if s.selected:
            return M_NEXT_SELECTED.format(pos=s.selected)
        return M_NEXT_START

    def _add_lead(self, e: _Entry, text: str, hear_out: dict) -> None:
        """Claude の言い添え（claude_lead）：プログラムの答え（hear_out の status・message）はそのまま。前に出す一言だけを足す。
        一言は表示と作業ログだけ。Session・候補・承認・会話の状態には使わない。"""
        with e.lock:
            if e.closed:  # 読み直しで閉じた画面：呼ばない
                return
            if not sim._is_synthetic(e.session.original_bytes):  # 合成でないジョブでは Claude を呼ばない（docs/CLAUDE_API.md 8-3）
                e.session.log.append(("Claude の言い添え", "出していません（合成でないジョブのため呼びません）"))
                return
            history = list(e.lead_history)
        r = claude_lead.lead(self.explainer, history, text, hear_out.get("status"))
        with e.lock:
            if e.closed:  # Claude を待つ間に読み直しで閉じられた：書き戻さない
                return
            if r["text"] is not None:
                hear_out["claude_lead"] = {"text": r["text"], "model": r["model"], "cost_usd": r["cost_usd"]}
                e.session.log.append(("Claude の言い添え", r["text"]))
            else:
                e.session.log.append(("Claude の言い添え", f"出していません（{r['why']}）"))

    def _remember_talk(self, e: _Entry, text: str, ask_out: dict | None, hear_out: dict | None) -> None:
        """言い添えに送る前の会話：人の発話と、前に選んだ相づちだけ（アプリ・説明の文は座標の値などが入るので残さない）。
        メモリの中だけ。"""
        h = e.lead_history
        h.append({"who": "人", "text": text})
        lead = hear_out.get("claude_lead") if isinstance(hear_out, dict) else None
        if lead:
            h.append({"who": "Claude", "text": lead["text"]})
        del h[:-claude_lead.HISTORY]

    def _talk_route(self, e: _Entry, text: str) -> str:
        """振り分け（決定論）："hear"（指示の道）・"count"（回数の変更の希望）・"ask"（会話の道）。"""
        s = e.session
        f = ut.read_utterance(text)
        count = talk.read_count(text, talk.repeat_blocks(s.original), e.talk.last_evidence)
        change = bool(set(f.kinds) & {"move", "speed", "select", "undo", "compare"} or f.axes or f.amounts or f.positions
                      or f.place_ref or count is not None or rc.is_repeat_request(text))
        approved = self._approved(e)
        if self._several_changes(f, count, text):
            return "several"  # 変更が2つ以上：どれも受け付けない（どの状態でも。承認も取り消さない）
        nt = ut.normalize(text)
        if _PURPOSE_SAVE.search(nt) and not _OUTPUT_WORDS.search(nt):
            # 作業目的の意味付け（「〜として登録して」）：変更の言葉もあればあいまい → 確かめる。無ければ会話の道（意味を教える）
            return "purpose_or_change" if (change and count is None) or count is not None else "ask"
        if not approved and (e.dialog.step is not None or s.repeat_range is not None):
            return "hear"  # 会話・範囲の確認の続き（「承認」と言ったときの2段階の案内も、今の道が出す）
        if _BUTTON_ONLY_WORDS.search(ut.normalize(text)) or (approved and f.stop_reasons):
            return "button"  # 承認・出力・起動など：状態を変えず（承認も取り消さず）、ボタンで行うことを伝えるだけ
        if _NEXT_Q.search(nt) and not change:
            return "next"  # K2：次にすることの質問（変更の言葉が無いときだけ）。決まった文で答える・状態は変えない
        if _BARE_NUMBER.fullmatch(nt) and not s.awaiting_answer and e.talk.pending is None:
            return "bare_number"  # K3：数だけ。聞き返しの答え（「何ミリですか？」→「3」）は今までの道
        # 止める理由のある文（「前・後ろ」など）は、承認の前なら今までどおり指示の道へ（止める文・場所の言葉の会話は指示の道が出す）
        if approved and not change:
            if f.question or _QUESTION_WORDS.search(ut.normalize(text)):
                return "ask"  # 承認のあとの質問：説明だけ（読み取りだけ。承認は取り消さない）
            return "approved"  # 承認のあとの相づち（「はい」「OK」）：承認を取り消さず、次にすることを伝えるだけ
        if e.dialog.step is not None or s.repeat_range is not None:
            return "hear"  # 承認・起動などは今の決まった文で止める／会話・範囲の確認の続きは今の道で
        if e.talk.pending is not None:
            return "ask"  # 「どの動きのことですか？」の途中（ボタンで確かめる）
        if s.candidate is None and s.awaiting_answer and ut.is_sign_only_answer(f):
            return "hear"  # 「プラスですか、マイナスですか？」への「マイナス」：指示の道で前の発話に足す（C1。11/5）
        if count is not None and _said_range(text):
            return "hear"  # 位置・範囲を言った回数の文（P1-C）：言ったとおりの範囲を指示の道が調べる（Claude を呼ばない）
        if count is not None and not (f.repeat and f.repeat[1] == "ambiguous" and "move" in f.kinds):
            return "count"  # 「2回移動させて」（回数の移動か往復か、あいまい）は、指示の道が2択で聞き返す
        if rc.is_repeat_request(text) or f.kinds or f.axes or f.positions or f.amounts or f.place_ref or f.shimasu_text:
            return "hear"  # プログラムが指示として読める（Claude を呼ばない）
        return "ask"

    @staticmethod
    def _several_changes(f, count, text: str) -> bool:
        """1つの文に、回数の変更と、位置・速度・元に戻す・比べる・選ぶ のどれかが一緒にあるか（P1-B）。"""
        repeat = count is not None or rc.is_repeat_request(text)
        # 位置の移動は、量か軸があるときだけ別の変更と数える（「C00001からC00002を2回移動させて」は1つのあいまいな指示：
        # 指示の道が［2回の移動］［2往復］で聞き返す）
        other = bool(set(f.kinds) & {"speed", "undo", "compare"} or f.amounts or f.axes)
        return repeat and other

    def _repeat_canonical(self, e: _Entry, text: str) -> str | None:
        """回数の変更の希望から、変更の道へ渡す決まった文を作る（プログラムが読んだ区間・今の回数・利用者の文の回数だけ）。"""
        s = e.session
        blocks = talk.repeat_blocks(s.original)
        count = talk.read_count(text, blocks, e.talk.last_evidence)
        if count is None or count.stop or count.keep or count.total is None:
            return None
        if _said_range(text):
            return text  # 人が言った範囲は置き換えない（P1-C）：利用者の文のまま変更の道へ（言った範囲だけを調べる）
        block = count.target or (blocks[0] if len(blocks) == 1 else None)
        if block is None:
            return text  # 区間が決まらない：利用者の文のまま変更の道へ（範囲を聞き返す）
        return rc.canonical(block.positions[0], block.positions[-1], block.count, count.total)

    def ask(self, sid: object, text: object = None, reset: object = False, info: object = False,
            source: object = "text", choose: object = None, token: object = None, sync: object = False,
            via_talk: bool = False) -> dict:
        """読み込んだジョブについて Claude に聞く。Session（候補・承認・選択・作業ログ）には触らない。
        会話の文脈と人が教えた意味は、この画面の中だけ（保存しない）。データ（ジョブ・候補・承認・上下の向きの照合）が
        変わったら、会話を区切る（新しい会話にして「データが変わったので…」と知らせる）。

        - sync：データの変化を確かめ、人が教えた意味と知らせだけを返す（Claude を呼ばない）
        - choose・token：「どの動きのことですか？」のボタン（Claude を呼ばない。番号が今のものと合うときだけ）
        - source：voice か text（どちらも同じ検査を通る。記録して返すだけ）
        """
        e = self._live(sid)
        if e is None:
            return self._closed_reply(sid, e)
        if info is True:
            with e.lock:
                if e.closed:
                    return self._closed_reply(sid, e)
                notices = self._talk_sync(e)
                tstate = self._talk_state(e, notices)
            return {"ok": True, "status": "info", **self.explainer.info(), "talk": tstate}
        if source not in SOURCES:
            return {"ok": False, "error": "聞く文の種類（voice か text）が分かりません"}
        notice = None
        with e.lock:  # 状態は読むだけ。Claude を呼ぶ間はこの錠を持たない（ほかの操作を止めない）
            if e.closed:
                return self._closed_reply(sid, e)
            s = e.session
            notices = self._talk_sync(e)
            if sync is True:
                return {"ok": True, "status": "sync", "talk": self._talk_state(e, notices)}
            if choose is not None:
                o = e.talk.choose(choose, token, s.original, self._current_job(e))
                return self._talk_reply(e, o, notices, source)
            st = s.updown_status()
            key = (hashlib.sha256(s.original_bytes).hexdigest(), st.token)
            if reset is True or e.explain is None or e.explain.key != key:
                if e.explain is not None and e.explain.turns and reset is not True:
                    notice = "ジョブか上下の向きの設定が変わったため、新しい会話にしました"
                e.explain = claude_explain.Conversation.start(s.original_bytes, s.original, st)
                if e.explain.facts is not None:
                    e.explain.facts["change_state"] = self._change_state(e)
                e.talk.last_evidence = ()
            if reset is True:
                e.talk.clear()  # 会話をやり直す：人が教えた意味も、この会話の中だけなので消す
                e.lead_history.clear()  # Claude の言い添えに送る前の会話も消す
                return {"ok": True, "status": "reset", **self.explainer.info(),
                        "talk": self._talk_state(e, notices)}
            conv = e.explain
            if not isinstance(text, str) or not text.strip() or len(text) > claude_explain.MAX_TEXT:
                return {**self.explainer.ask(conv, text), "talk": self._talk_state(e, notices)}
            text = text.strip()
            # via_talk：talk の入口が中で呼んだとき（talk の側で取消・言い直しをもう見分けてある）。/api/ask の Option 1 は使わない
            pre = self._stale_check(e, text, source, said_by_person=True, from_ask=not via_talk)
            if pre is not None:
                # /api/ask にも取消・言い直しが届く（P0 設計 0-2 (2)）：無効・保留にしてから答える。説明の頼みなら説明の節だけを聞く
                summary, tail = pre.pop("summary", None), pre.pop("tail", "")
                explain = pre.pop("explain", None)
                if explain is None:
                    out = self._app_reply(e, pre["message"], notices, source, "stale_" + pre["stale"], text)
                    out["stale"] = pre["stale"]
                    out["choices"] = pre.get("choices") or []  # 保留なら［取り消す］［このまま残す］（押すと決まった文を /api/hear へ）
                    return out
                notices = notices + [M_CXEX.format(summary=summary) + tail]
                text = explain
                if e.explain is None or e.explain.key != key:
                    e.explain = claude_explain.Conversation.start(s.original_bytes, s.original, st)
                conv = e.explain
                if conv.facts is not None:
                    conv.facts["change_state"] = self._change_state(e)
            s.forget_question()  # Claude に聞いた発話のあとに、前の聞き返しへの答えを当てない（Safety F1。/api/ask を直接呼んだときも）
            pending = s.candidate is not None
            blocks = talk.repeat_blocks(s.original)
            count = talk.read_count(text, blocks, e.talk.last_evidence)
            if count is not None and count.stop:
                # 回数の言葉に打ち消し・ためらい（「3回にしないで」「そう、じゃなくて4回」「たぶん3回」）：読まない・呼ばない
                stop = count.stop
                if pending and count.relative:
                    stop = talk.COUNT_MORE_PENDING  # 「あと1回」：承認待ちの候補があることを知らせる（Safety F2）
                return self._app_reply(e, stop, notices, source, "count_refused", text)
            if pending and talk.looks_like_change(text):
                # Existing candidate is protected; a numeric clarification above
                # must not be replaced with instructions to approve this candidate.
                return self._app_reply(e, talk.PENDING_EXPLAIN_ONLY, notices, source, "explain_only", text)
            o = e.talk.consider(text, s.original, self._current_job(e))
            if o.kind in ("ask", "refused"):
                return self._talk_reply(e, o, notices, source, said=text)
            taught_note = o.message if o.kind in ("taught", "replaced") else None
            taught = e.talk.facts_for_claude(s.original)
            version = e.talk.signature
        out = self.explainer.ask(conv, text, taught=taught, allow_change=not pending, count=count)
        with e.lock:
            if e.closed:  # Claude に聞いている間に読み直しで閉じられた：答えを書き戻さない・返さない
                return self._closed_reply(sid, e)
            if out.get("ok") and e.explain is conv and e.talk.signature is version:
                e.talk.last_evidence = tuple(ev["line"] for ev in out.get("evidence", []))
            hl, sup = talk.highlight_and_supplement(conv.job, out.get("evidence", []) if out.get("ok") else [])
            out.update(highlight=hl, supplement=sup, source=source, heard=text,
                       taught_note=taught_note, talk=self._talk_state(e, notices))
        if notice:
            out["conversation_notice"] = notice
        return out

    def _current_job(self, e: _Entry) -> jbi.Job:
        """今のデータ：承認待ち・承認済みの候補があれば、その変更後のジョブ。無ければ読み込んだジョブ。"""
        ch = e.session.candidate
        return ch.new_job if ch is not None and getattr(ch, "new_job", None) is not None else e.session.original

    def _change_state(self, e: _Entry) -> dict:
        ch = e.session.candidate
        kinds = {"shift": "位置の移動", "repeat": "回数の変更", "speed": "速度の変更"}
        return {"pending_candidate": ch is not None and not self._approved(e), "approved": self._approved(e),
                "kind": kinds.get(getattr(ch, "kind", None)) if ch is not None else None,
                "note": "説明は、読み込んだ元の動作データについて。変更はアプリのプログラムと承認ボタンで行う"}

    def _talk_sync(self, e: _Entry) -> list[str]:
        """データ（ジョブの中身・候補・承認）が変わったか。変わったら、意味を確かめ直し、会話を区切る。知らせの文を返す。"""
        s = e.session
        sig = (e.talk.content_hash, s.candidate, e.approval)
        if e.talk.sync(sig, self._current_job(e)):
            if e.explain is not None and e.explain.turns:
                e.talk.notices.insert(0, talk.DATA_CHANGED)
            e.explain = None  # 会話を区切る：次に聞くときは新しい事実（変更候補の状態も）で始める
        return e.talk.take_notices()

    def _talk_state(self, e: _Entry, notices: list[str]) -> dict:
        return {**e.talk.state(self._current_job(e)), "notices": list(notices)}

    def _app_reply(self, e: _Entry, message: str, notices: list[str], source: str, status: str,
                   heard: str | None = None) -> dict:
        """アプリの決まった文で答える（Claude は呼ばない）。"""
        return {"ok": True, "status": status, "reply": message, "reply_by": "app", "checked": None, "notes": [],
                "evidence": [], "intent": None, "change_request": None, "change_record": None, "guesses": [],
                "highlight": {"lines": [], "positions": []}, "supplement": [], "source": source, "heard": heard,
                "taught_note": None, "talk": self._talk_state(e, notices)}

    def _talk_reply(self, e: _Entry, o: "talk.Outcome", notices: list[str], source: str,
                    said: str | None = None) -> dict:
        status = {"ask": "ask_which", "refused": "not_taught", "taught": "taught", "replaced": "taught"}[o.kind]
        out = self._app_reply(e, o.message or "", notices, source, status, said)
        if o.kind in ("taught", "replaced"):  # ボタンで選んで登録した：アプリの知らせだけ（Claude は呼ばない）
            out["taught_note"], out["reply"] = o.message, ""
        if o.block is not None:  # 確かめている区間を光らせる（行番号からプログラムが決める）
            out["highlight"], out["supplement"] = talk.highlight_and_supplement(
                e.session.original, [{"line": o.block.first_line}])
        return out

    # ---- 内部 ----
    def _close_all_locked(self) -> list:
        """今あるすべての画面を _entries から外し、閉じた印を立てる（self._lock を持って呼ぶ。画面の錠は取らない）。"""
        closing = list(self._entries.items())
        self._entries.clear()
        for old_sid, old in closing:
            old.closed, old.closed_reason = True, "replaced"
            self._closed[old_sid] = "replaced"
            self._closed.move_to_end(old_sid)
        while len(self._closed) > _CLOSED_KEEP:
            self._closed.popitem(last=False)
        return closing

    @staticmethod
    def _discard_closed(e: _Entry) -> None:
        """閉じた画面の候補・承認・範囲の確認・会話を捨てる（その画面の錠を取って。self._lock は持たない）。"""
        with e.lock:
            e.session._discard()
            e.session.forget_range()
            e.session.forget_question()
            sim.revoke(e.approval)  # 閉じた画面の承認は、手に持ったままでも出力できない（P0 設計 0-2 (1) 層2）
            e.sim_proposal = e.approval = e.approved_candidate = e.approval_updown = None
            e.approval_gen = None
            e.hold = False
            e.cand_gen += 1
            e.comparison = None
            e.dialog = dg.Dialog()
            e.explain = None
            if e.talk is not None:
                e.talk.clear()

    # ---- P0 H02・H16：取消・言い直しで、前の候補を無効にする（docs/DESIGN_P0_STALE_CANDIDATES_20261105.md） ----
    @staticmethod
    def _touch_gen(e: _Entry) -> None:
        """候補が変わっていれば（どの道で変わっても）世代を1つ進める。保留を解き、新しい候補なら無効の記録を消す。"""
        ch = e.session.candidate
        if ch is not e.cand_seen:
            e.cand_gen += 1
            e.cand_seen = ch
            e.hold = False
            if ch is not None:
                e.voided = None

    def _voided_note(self, e: _Entry) -> str:
        v = e.voided
        return M_VOIDED.format(why=v["why"], summary=v["summary"]) if v else ""

    def _invalidate(self, e: _Entry, why: str) -> dict:
        """前の候補を無効にする（錠の中で呼ぶ）：承認を取り消した印を立てて消す・候補・範囲の確認・会話・答え待ちを捨てる・
        世代を進める。元の JOB と、出力したファイルには触らない。無効にした記録を返す。"""
        s = e.session
        ch = s.candidate
        exported = e.exported
        v = {"why": why, "summary": _summary(ch), "had_approval": e.approval is not None,
             "exported_name": Path(exported).name if exported else None, "kind": getattr(ch, "kind", None),
             "before_count": getattr(ch, "before_count", None)}
        self._cancel_approval(e, f"{why}のため")
        s.undo()  # 答え待ち・範囲の確認・候補を捨てる（今の関数）
        e.dialog.end()
        e.dialog.estimated = None
        e.comparison = None
        e.exported = None
        e.hold = False
        e.cand_gen += 1
        e.cand_seen = s.candidate
        v["gen"] = e.cand_gen
        e.voided = v
        s.log.append(("取消", f"{why}のため、変更候補を取り消しました（{v['summary']}）。承認・出力はできません。"
                             "元の動作データは変わっていません"))
        return v

    @staticmethod
    def _void_tail(v: dict) -> str:
        return ((M_HAD_APPROVAL if v["had_approval"] else "")
                + (M_HAD_EXPORT.format(name=v["exported_name"]) if v["exported_name"] else ""))

    def _stale_check(self, e: _Entry, text: str, source: str, said_by_person: bool = False,
                     remake: bool = False, from_ask: bool = False) -> "dict | None":
        """取消・言い直し・保留の見分け（プログラム。錠の中で、文を受け取る入口の最初に呼ぶ）。
        候補が無いとき・今の道のままのときは None。扱ったときは、指示の道の答えの形（hear と同じ）を返す。
        取消＋説明なら "explain"（説明の節）・"summary"・"tail" を入れて返す（呼んだ側が使って外す）。"""
        s = e.session
        self._touch_gen(e)
        if not isinstance(text, str) or not text.strip():
            return None
        text = text.strip()
        if e.hold and s.candidate is not None and stale.is_keep(text):
            had = self._cancel_approval(e, "候補を残すと言われたため（承認はもう一度、承認ボタンで）")
            e.hold = False
            msg = (M_KEPT_APPROVAL if had else "") + M_KEPT.format(summary=_summary(s.candidate))
            return self._stale_out(e, text, source, "ok", msg, stale.KEEP)
        if s.candidate is None:
            return None
        said = text
        ch = s.candidate
        if ch.kind == "shift" and ch.target.split()[0]:
            # 画面の選択肢の文（「C00005を元に戻します」）：今の候補の位置と同じときだけ、頭の「C00005を」を外して見分ける
            # （今の _instruction と同じ。別の位置の名前は外さない）
            said = re.sub(rf"^{re.escape(ch.target.split()[0])}を", "", ut.normalize(text))
        v = stale.classify(said)
        if v.kind == stale.CANCEL:
            info = self._invalidate(e, "取消")
            msg = (APPROVAL_AND_CANDIDATE_CANCELLED if info["had_approval"]
                   else "変更候補を捨てました。元のジョブは変わっていません")
            if info["exported_name"]:
                msg += M_HAD_EXPORT.format(name=info["exported_name"])
            return self._stale_out(e, text, source, "ok", msg, v.kind)
        if from_ask and ((v.rule.startswith("10") and v.kind == stale.CORRECTION) or v.kind == stale.UNSURE):
            # /api/ask（読み取りの入口）に、印の無い変更の指示・承認した中身への確認の質問（Option 1・11/6 唐川さんの決定）：
            # 候補は作らない・前の承認を取り消して（取り消しの印）、候補を保留にする（承認・出力できない）
            return self._ask_hold(e, text, source)
        if v.kind == stale.CORRECTION:
            info = self._invalidate(e, "言い直し")
            if remake and v.rest and _complete_instruction(v.rest):
                # 仕様 A（11/6 12:29 唐川さんの決定）：完全な新しい指示（対象・軸・向き・量が分かる）は、古い候補・承認を
                # 失効したうえで、新しい内容だけをふつうの指示の道へ通す（上限・向き・単位などの検査を全部通す。承認はボタンだけ）。
                # 呼ぶのは talk・hear の入口だけ（ask は読み取りの入口なので作り直さない）
                out = self._hear_body(e, v.rest, source)
                out["message"] = M_CORR_REMADE.format(summary=info["summary"]) + self._void_tail(info) + (out.get("message") or "")
                out["heard"], out["stale"], out["remade_from"] = text, v.kind, v.rest
                return out
            msg = M_CORR.format(summary=info["summary"]) + self._void_tail(info) + self._corr_next(info, v.value)
            return self._stale_out(e, text, source, "ask", msg, v.kind)
        if v.kind == stale.CANCEL_EXPLAIN:
            info = self._invalidate(e, "取消")
            tail = self._void_tail(info)
            out = self._stale_out(e, text, source, "ok", M_CXEX.format(summary=info["summary"]) + tail, v.kind)
            out.update(explain=v.explain, summary=info["summary"], tail=tail)
            return out
        if v.kind == stale.UNSURE:
            revoked = False
            if _NEGATION_ONLY.search(ut.normalize(said)) or _NEGATION_WHOLE.fullmatch(_bare(said)):
                # D3（11/6 唐川さんの決定）：「だめ」「ノー」などの否定は、少なくとも承認を無効にする（保留のうえで取り消す）
                revoked = self._cancel_approval(e, "否定の言葉があったため")
            if v.rule.startswith("10"):
                # Q-3（規則 10）の保留：新しい変更の指示かもしれない。承認も取り消す（取り消しの印を立てる・2層目。
                # 前は hear の道で承認を取り消していた。安全を保留の印 1つだけに頼らない：11/6 Safety レビュー F1）
                revoked = self._cancel_approval(e, "新しい変更の指示かもしれないため") or revoked
            e.hold = True
            s.log.append(("保留", "取り消すか確かめるまで、変更候補を保留にしました（承認・出力はできません）"))
            if v.asked_form:
                return None  # 「元に戻しますか」：今の道が［「元に戻します」でよいですか？］で確かめる（押すと取消）。保留のまま
            if v.rule == "10 more":
                # K1：足りないもの（量・向き／回数）を聞く。新しい候補は作らない・前の値は引き継がない
                more = M_MORE_REPEAT if s.candidate.kind == "repeat" else M_MORE_SHIFT
                msg = (M_HOLD_REVOKED if revoked else "") + more.format(summary=_summary(s.candidate))
                return self._stale_out(e, text, source, "ask", msg, v.kind)
            msg = (M_HOLD_REVOKED if revoked else "") + M_UNSURE.format(summary=_summary(s.candidate))  # 承認は黙って消さない
            return self._stale_out(e, text, source, "ask", msg, v.kind)
        if v.kind == stale.QUESTION_ABOUT_CANCEL:
            return self._stale_out(e, text, source, "ok", M_HOWCX, v.kind)
        bare = _bare(said)
        if v.kind == stale.OTHER and _NEGATION_WHOLE.fullmatch(bare):
            # XC-02・D3（11/6 唐川さんの決定）：候補があるときの「だめ」「ノー」「出さないで」など（それだけの文）。
            # 未承認でも承認できない状態（保留）にする。承認済みなら承認も取り消す。取り消すかは［取り消す］［このまま残す］で
            revoked = self._cancel_approval(e, "否定の言葉があったため")
            e.hold = True
            s.log.append(("保留", "否定の言葉があったため、変更候補を保留にしました（承認・出力はできません）"))
            return self._stale_out(e, text, source, "ask", (M_HOLD_REVOKED if revoked else "")
                                   + M_UNSURE.format(summary=_summary(s.candidate)), stale.UNSURE)
        if v.kind == stale.OTHER and self._approved(e) and _HESITATION.search(bare):
            # XC-03：承認済みの「どうしよう」「どうかな」「どうだろう」：ためらい。保留にして確かめる（出力はできない）
            e.hold = True
            s.log.append(("保留", "承認のあとのためらいの言葉のため、変更候補を保留にしました（承認・出力はできません）"))
            return self._stale_out(e, text, source, "ask", M_UNSURE.format(summary=_summary(s.candidate)), stale.UNSURE)
        if (from_ask and v.kind == stale.OTHER and v.rule != "3c question" and s.candidate is not None and talk.looks_like_change(said)
                and not stale.is_ack(said) and not stale.is_plain_question(said) and not stale.is_plain_button_request(said)):
            # ask に変更の意思のある文（承認済みでも未承認でも）：ask には指示の道（hear）が無いので、ここで保留にする
            # （f42676a では承認が残り、ボタンで古い中身を出力できた：CHECKER_039 の ask 23件）
            return self._ask_hold(e, text, source)
        if (said_by_person and v.kind == stale.OTHER and self._approved(e) and not stale.is_ack(said) and not stale.is_plain_question(said)
                and not stale.is_plain_button_request(said) and self._talk_route(e, text) != "hear"):
            # 承認済みの候補があるとき、承認を残す文は、はっきりした相づち・否定の無い質問・否定の無い「出力して」だけ。
            # 指示の道（hear）は今までどおり承認を取り消す。ほかの道（相づち・説明・ボタンの案内・回数）は保留（Safety 再検証：
            # 言葉の一覧に足す直し方では「消しといて」「中止」「違くない？」などが抜ける）。ほかの文は保留にして確かめる
            if from_ask:
                # /api/ask（Option 1）：保留なら前の承認も取り消す（返事の文と状態を一致させる。CHECKER_039「うーん」）
                return self._ask_hold(e, text, source)
            e.hold = True
            s.log.append(("保留", "承認のあとの文が相づち・ふつうの質問ではないため、変更候補を保留にしました（承認・出力はできません）"))
            revoked = (bool(_NEGATION_ONLY.search(ut.normalize(said)) or _NEGATION_WHOLE.fullmatch(_bare(said)))
                       and self._cancel_approval(e, "否定の言葉があったため"))
            return self._stale_out(e, text, source, "ask", (M_HOLD_REVOKED if revoked else "")
                                   + M_UNSURE.format(summary=_summary(s.candidate)), stale.UNSURE)
        return None

    def _ask_hold(self, e: _Entry, text: str, source: str) -> dict:
        """ask の保留（Option 1）：前の承認を取り消し（取り消しの印を立てる）、候補を保留にする。返事は決まった文で、状態と一致させる。"""
        s = e.session
        revoked = self._cancel_approval(e, "説明の欄に変更の指示・確かめの質問があったため")
        e.hold = True
        s.log.append(("保留", "説明の欄に変更の指示・確かめの質問があったため、変更候補を保留にしました（承認・出力はできません）"))
        msg = M_ASK_HOLD.format(revoked=M_ASK_REVOKED if revoked else "", summary=_summary(s.candidate))
        return self._stale_out(e, text, source, "ask", msg, stale.UNSURE)

    @staticmethod
    def _corr_next(info: dict, value: str | None) -> str:
        """言い直しのあとに、次にすることの文（新しい候補は作らない）。"""
        if info["kind"] == "repeat" and value and value.endswith("回"):
            n = value[:-1]
            if not n.isdigit():
                return M_CORR_DECIMAL
            if info["before_count"] is not None and int(n) == info["before_count"]:
                return M_CORR_SAME.format(n=int(n))
            return M_CORR_COUNT.format(n=int(n))
        if info["kind"] == "shift":
            return M_CORR_MOVE
        return M_CORR_OTHER

    # 保留の返事のボタン（QA 再レビュー M-2：候補を確かめている間は入力の欄・マイクが無いことがある。ボタンで答えられるように）。
    # 押すと決まった文を /api/hear へ送る（今の画面の選択肢と同じ道）。「このままでいい」は承認ではない（承認は消える）
    HOLD_CHOICES = (("取り消す", "取り消して"), ("このまま残す（もう一度承認）", "このままでいい"))

    def _stale_out(self, e: _Entry, text: str, source: str, status: str, msg: str, kind: str) -> dict:
        s = e.session
        if (kind == stale.UNSURE and s.candidate is not None and ut.read_utterance(text).question
                and not _NEGATION_WHOLE.fullmatch(_bare(text))):
            # XC-07・D3（11/6）：「大丈夫？」「合ってる？」などの確かめの質問には、まず質問として答える（今の候補の中身と、
            # 決めるのは人のボタンであること）。状態は保留のまま（承認・出力はできない）。安全の保証はしない
            msg = M_Q_ANSWER.format(summary=_summary(s.candidate)) + msg
        s.log.append(("発話", f"{'音声' if source == 'voice' else '文字'}：{text}"))
        s.log.append(("聞き返し" if status == "ask" else "結果", msg))
        s.facts = ut.read_utterance(text)  # 状態の「最後に聞いた文」の読み取りも、この発話にそろえる（表示。Session.hear と同じ）
        return {"ok": True, "status": status, "message": msg, "heard": text, "source": source,
                "facts": _facts(s.facts),
                "tool_calls": [], "dialog": False, "blocked_reason": None, "candidate_made": False,
                "choices": ([{"label": a, "text": b} for a, b in self.HOLD_CHOICES] if kind == stale.UNSURE else []),
                "no_change": kind not in stale.INVALIDATING, "stale": kind,
                "tone": "guide"}  # N-2：保留・取り消しの知らせはエラーの見出しで出さない（状態・判定は変えない）

    def _closed_reply(self, sid: object, e: "_Entry | None" = None) -> dict:
        """閉じた画面・見つからない画面への要求を断る（状態は変えない・返さない）。
        画面の錠を持っているときは e を渡す（self._lock を取らない。錠の順番を _run と逆にしない）。"""
        if e is not None and e.closed:
            replaced = True
        else:
            with self._lock:
                replaced = isinstance(sid, str) and sid in self._closed
        return {"ok": False, "error": SESSION_CLOSED if replaced else NO_SESSION, "session_closed": True}

    def _live(self, sid: object) -> "_Entry | None":
        """今の画面（閉じた印が無いもの）。無ければ None。"""
        with self._lock:
            e = self._entries.get(sid) if isinstance(sid, str) else None
            return None if e is None or e.closed else e

    def _run(self, sid: object, fn) -> dict:
        with self._lock:
            e = self._entries.get(sid) if isinstance(sid, str) else None
            if e is not None and not e.closed:
                self._entries.move_to_end(sid)
        if e is None or e.closed:
            return self._closed_reply(sid, e)
        with e.lock:
            if e.closed:  # 錠を待つ間に読み直しで閉じられた：何もしない
                return self._closed_reply(sid, e)
            st = e.session.updown_status()  # 要求のたびに、上下の向きの設定を読み直す
            e.dialog.direction_names = st.direction_names
            out = fn(e)
            self._sync(e)
            out["state"] = self._state(sid, e)
            return out

    def _fail(self, e: _Entry, message: str) -> dict:
        e.session.log.append(("止めた理由", message))
        return {"ok": False, "error": message}

    def _remember(self, e: _Entry, r: Reply) -> None:
        if r.comparison is not None and e.session.candidate is not None:
            e.comparison = (e.session.candidate, r.comparison)

    def _after_approval(self, e: _Entry) -> dict:
        """承認のあとに並べて出す状態（プログラムの決まった文だけ。表示だけで、承認・出力の条件は変えない）。

        「元の動作データ：変わっていません」は、元のジョブを書き戻したバイト列と、Session が最初に取った写しが、
        どちらも読み込んだときのバイト列（の sha256）と同じときだけ出す。確かめられなければ行を出さずに理由（note）を出す。
        """
        s = e.session
        same = (e.loaded_sha256 is not None
                and hashlib.sha256(s.original.render_bytes()).hexdigest() == e.loaded_sha256
                and hashlib.sha256(s.original_bytes).hexdigest() == e.loaded_sha256)
        rows = ([{"label": "元の動作データ", "value": ORIGINAL_UNCHANGED}] if same else []) + [
            {"label": "変更候補", "value": CANDIDATE_APPROVED}, {"label": "ロボットへの送信", "value": NOT_SENT}]
        exported = EXPORTED_LINE.format(name=Path(e.exported).name) if e.exported else None
        return {"rows": rows, "note": None if same else ORIGINAL_UNKNOWN_NOTE, "exported": exported}

    def _approved(self, e: _Entry) -> bool:
        return (e.approval is not None and e.sim_proposal is not None
                and e.session.candidate is not None and e.approved_candidate is e.session.candidate)

    def _updown_unchanged(self, e: _Entry) -> bool:
        """承認したときの上下の向きの照合の結果と、今（読み直した）結果が同じか。"""
        return e.approval_updown is not None and e.approval_updown.token == e.session.updown_status().token

    def _begin_instruction(self, e: _Entry) -> bool:
        """新しい指示の始め：承認を今どおり取り消す。承認していたときは、承認待ちの文を決まった文にする（S-msg）。
        承認を実際に取り消したら True（返事の頭に決まった文を付けるため。_say_cancelled）。"""
        e.session.pending_message = APPROVED_PENDING_MESSAGE if self._approved(e) else PENDING_MESSAGE
        e.session.forget_range()  # 回数の変更の範囲の確認も捨てる（古いボタンを押しても候補にならない）
        return self._cancel_approval(e, "新しい指示があったため")

    def _say_cancelled(self, e: _Entry, out: dict) -> dict:
        """承認を実際に取り消した返事の頭に、文を付ける（唐川さんの決定②。承認していなければ呼ばない）。
        変更候補が残っていれば決まった文（APPROVED_PENDING_MESSAGE）、候補も捨てていれば APPROVAL_AND_CANDIDATE_CANCELLED。
        候補を捨てたときは、同じことを言う返事の文（「変更候補を捨てました。元のジョブは変わっていません」など）を重ねない。
        会話の欄の最後の文が同じ返事なら、そちらにも付ける。"""
        kept = e.session.candidate is not None
        head = APPROVED_PENDING_MESSAGE if kept else APPROVAL_AND_CANDIDATE_CANCELLED
        msg = out.get("message") or ""
        if msg.startswith(head):
            return out
        new = msg
        if not kept:
            for phrase in _DROPPED_PHRASES:
                new = new.replace(phrase, "")
        out["message"] = head + new
        turns = e.dialog.turns
        if out.get("dialog") and turns and turns[-1].get("text") == msg and turns[-1].get("who") != "人":
            turns[-1] = {**turns[-1], "text": head + new}
        return out

    def _cancel_approval(self, e: _Entry, why: str) -> bool:
        """承認を取り消す。実際に取り消したら True。"""
        if why != UPDOWN_CHANGED_WHY:
            e.updown_cancelled = False  # 新しい指示・選び直し・元に戻す：上下の文はもう出さない
        if e.approval is None:
            return False
        e.session.forget_question()  # 承認の取り消し：未確定の聞き返しも捨てる（CHECKER_028 A）
        e.updown_cancelled = why == UPDOWN_CHANGED_WHY
        sim.revoke(e.approval)  # 捨てる前に印を立てる：古い承認を手に持ったまま出力しても書かない（P0 設計 0-2 (1) 層2）
        e.approval = e.sim_proposal = e.approved_candidate = e.approval_updown = None
        e.approval_gen = None
        e.session.log.append(("承認", f"{why}、承認を取り消しました。もう一度確かめて、承認ボタンで承認してください"))
        return True

    def _sync(self, e: _Entry) -> None:
        self._touch_gen(e)
        if e.approval is not None and not self._approved(e):
            self._cancel_approval(e, "変更候補が変わったため")
        if e.approval is not None and not self._updown_unchanged(e):
            self._cancel_approval(e, UPDOWN_CHANGED_WHY)

    def _reply(self, e: _Entry, r: Reply) -> dict:
        return {"status": r.status, "message": r.message, "blocked_reason": r.blocked_reason,
                "candidate_made": r.candidate_made,
                "choices": [{"label": label, "text": text} for label, text in r.choices]}

    def _state(self, sid: str, e: _Entry) -> dict:
        s = e.session
        self._touch_gen(e)
        cand = None
        ch = s.candidate
        if ch is not None:
            if e.comparison is None or e.comparison[0] is not ch:
                cand = None
            else:
                cand = _comparison_dict(e.comparison[1], ch, s)
                est = e.dialog.estimated
                cand["estimated"] = est if est and est.get("position") == cand["target"].split()[0] else None
        approved = self._approved(e)
        st = s.updown_status()
        e.dialog.direction_names = st.direction_names
        same_updown = s.candidate_updown is not None and s.candidate_updown.token == st.token
        if cand is not None:
            # 候補の向きの名前（「（下へ）」）と、確認表示の「上下の向き：〇〇さんが確認」の1行は、候補を作ったときの照合の結果から。
            # 今の照合の結果と違えば（設定が変わった）名前は付けない（承認もできない）
            cu = s.candidate_updown
            cand["direction_names"] = cu.direction_names if (cu is not None and same_updown) else {}
            if s.candidate_facing:  # d：確かめた向きで読んだ発話から作った候補（候補の表示と確認表示の注意に出す。表示だけ）
                cand["warnings"] = list(cand["warnings"]) + [ut.FACING_CANDIDATE_NOTE]
            cand["updown"] = cu.as_state() if (cu is not None and same_updown) else None
        return {
            "session_id": sid,
            "mode": api.MODE,
            "job_name": s.original.name,
            "selected": s.selected,
            "facts": _facts(s.facts),
            # 確認済みの方向の名前だけ（上下の向きの照合に通ったときだけ Z の 上・下。右・前 などは入れない）
            "direction_names": st.direction_names, "direction_names_source": st.direction_names_source,
            # 上下の向きの照合の結果（CHECKER_026 M2）。画面の表示は別の担当
            # app_robot：アプリの --robot の値（どの状態でも。画面にロボット名を常に出すため。CHECKER_030 S30-1）
            "updown": {**st.as_state(), "app_robot": self.robot},
            # d：ロボットを正面から見た向き（この画面だけ。保存しない）
            "facing": {"confirmed": s.facing == "confirmed",
                       "message": ut.FACING_STATE_MESSAGE if s.facing == "confirmed" else None},
            "candidate": cand,
            "confirmation_text": s.confirmation_text(),
            "blocked_reason": s.candidate_blocked_reason if ch is not None else None,
            "approval": ({"approved_by": e.approval.approved_by, "approved_at": e.approval.approved_at,
                          "updown_status": e.approval.updown_status,
                          "updown_fingerprint": e.approval.updown_fingerprint,
                          "updown_confirmed_by": e.approval.updown_confirmed_by}
                         if approved else None),
            "can_approve": (ch is not None and ch.kind in ("shift", "repeat") and not approved
                            and not s.candidate_blocked_reason and same_updown and not e.hold),
            # P0：承認・出力のボタンが送る候補の世代。保留・無効の記録（要約だけ。プログラムが作った文）
            "candidate_gen": e.cand_gen,
            "hold": e.hold,
            "voided": ({"why": e.voided["why"], "summary": e.voided["summary"]} if e.voided and ch is None else None),
            # 出力できるのは合成ジョブ（samples/dummy と同じバイト列）だけ。実機の JOB は承認しても出力ボタンを押せない（画面で明示する）
            "exportable": sim._is_synthetic(s.original_bytes),
            # 出力のあとのボタンは、画面が state.exported で押せなくする（can_export は「承認が使える」の意味にも使うので変えない。
            # 11/7：ここを False にすると、画面が出力のあとを「保留中」と出した。2回目の出力はサーバーの export が断る）
            "can_export": approved and sim._is_synthetic(s.original_bytes) and not e.hold,
            "exported": e.exported if approved else None,
            # 承認のあとの3つの状態（台本 Ver.6.1 Scene 3-3）。表示だけ。承認していなければ None
            "after_approval": self._after_approval(e) if approved else None,
            "out_dir": str(Path(sim.DEFAULT_OUT_DIR).resolve()),
            "mock_notice": self.interpreter.notice,
            # 上の帯の MOCK の印（Issue #8 round4）：解釈の係か説明の係がモック・キーなしなら True。表示だけ（承認・出力の判定には使わない）
            "uses_mock": self.uses_mock(),
            "interpreter": {"kind": self.interpreter.kind, "model": self.interpreter.model,
                            "notice": self.interpreter.notice},
            "log": _log_entries(s.log),
            "dialog": self._dialog_state(e),
            # 回数の変更の範囲の確認（ボタンを待っている間だけ。行番号・命令の行・回数はプログラムが入れたもの）
            "repeat_range": _range_state(s.repeat_range),
            # いまの状態の1行（CHECKER_033 P1-A・唐川さんの方針①）：プログラムの確定情報だけから作る。Claude の文では変わらない
            "status_line": _status_line(ch is not None, bool(s.candidate_blocked_reason) if ch is not None else False,
                                        approved, bool(e.exported) if approved else False, s.repeat_range is not None,
                                        hold=e.hold and ch is not None,
                                        voided_why=e.voided["why"] if e.voided and ch is None else None),
        }

    def uses_mock(self) -> bool:
        """解釈の係か説明の係のどちらかが本物の Claude でないか（モック・台本の答え・キーなし）。上の帯の MOCK の印だけに使う。"""
        interp_mock = getattr(self.interpreter, "kind", None) != "claude"
        ex = self.explainer
        ex_mock = (not getattr(ex, "has_key", False)) or str(getattr(ex, "model", "") or "").startswith("claude-mock")
        return bool(interp_mock or ex_mock)

    def _dialog_state(self, e: _Entry) -> dict:
        d = e.dialog
        est = d.estimated if d.estimated and d.estimated.get("position") == e.session.selected else None
        on = d.step is not None
        return {
            "step": d.step, "turn": d.turn,
            "word": d.word if on else None, "relative": d.relative if on else None,
            "candidates": [c.as_dict() for c in d.candidates] if d.step == "confirm_position" else [],
            "choices": d.choices(),
            "position": d.position if on else None,
            "amount_mm": d.amount if on else None,
            "direction": d.direction if on else None,
            "estimated": est,  # 推定（未確定）。この画面の中だけ。保存しない
            "speaker": self._speaker(),
            "locator": {"kind": self.interpreter.kind,
                        "label": getattr(self.interpreter, "locator_label", None) or self._speaker()},
            "turns": list(d.turns),
        }


def _range_state(rr: dict | None) -> dict | None:
    if rr is None:
        return None
    keep = ("turn", "target", "total", "count", "positions", "cycle_lines", "line_nos", "block_line_nos",
            "yes_label", "no_label", "note", "side_note", "heading", "cycles", "button_note")
    return {k: rr[k] for k in keep}


def _updown_note(st: "ud.UpdownStatus | None") -> str:
    """承認・出力の作業ログに残す、上下の向きの設定の事実（状態・確認した人・指紋）。"""
    if st is None:
        return "上下の向きの設定：記録なし"
    if st.confirmed:
        return f"上下の向きの設定：確認済み（確認した人 {st.confirmed_by}・{st.confirmed_at}・指紋 {st.fingerprint}）"
    return f"上下の向きの設定：{_UPDOWN_STATUS_NAMES.get(st.status, st.status)}（上・下は Z として読んでいません）"


def _xyz(job: jbi.Job, name: str) -> list[str] | None:
    pos = job.positions.get(name)
    if pos is None or len(pos.values) < 3 or not pos.is_robot_coordinates():
        return None  # ロボット座標（直交）でなければ X・Y・Z として出さない
    return [f"{v:.3f}" for v in pos.xyz]


def _with_cycles(segments: list[dict], block: "jbi.RepeatBlock") -> list[dict]:
    """図の区間の線に、何回目の区間の線か（cycle。区間の外は None）を付ける。再生で「2回目」と数えるため。"""
    return [{**seg, "cycle": block.cycle_of(seg["line_no"] - 1)} for seg in segments]


def _beside_block(job: jbi.Job, block: "jbi.RepeatBlock") -> tuple[dict | None, dict | None]:
    """区間のすぐ前の移動と、すぐ後の移動（行き先の位置・行番号・命令の行。ジョブの行のまま）。無ければ None。"""
    moves = [i for i in job.instructions if api._is_move(i) and i.position]
    pick = lambda i: {"position": i.position, "line_no": i.index + 1, "text": i.text}  # noqa: E731
    entry = next((pick(i) for i in reversed(moves) if i.index < block.first_line), None)
    exit_ = next((pick(i) for i in moves if i.index >= block.end_line), None)
    return entry, exit_


def _repeat_sequence(original: jbi.Job, ch: "jbi.RepeatProposal") -> dict:
    """動きの並びのカード（［C00000（区間の前）］→［①］［②］［③ 追加］→［C00004（区間の後）］）と差分の要約の材料。
    すべて変更前後のジョブと区間（RepeatBlock）から作る。位置の意味（待機など）は決めつけない。
    name は区間の名前（今は target と同じ。人が教えた意味の名前を、あとで差し込めるように別に持つ）。"""
    def side(job: jbi.Job, block: "jbi.RepeatBlock") -> dict:
        entry, exit_ = _beside_block(job, block)
        return {"cycles": list(range(1, block.count + 1)), "entry": entry, "exit": exit_}
    by_line = {i.index: i for i in ch.new_job.instructions}
    added_ins = [by_line.get(n) for n, _ in ch.added]
    if any(i is None for i in added_ins):
        added_ins = []  # 足した行が命令として読めない（ありえないはず）：要約は出さない
    parts = []
    if added_ins:
        only_movl = all(i.command == "MOVL" for i in added_ins)
        parts.append(f"足した行：{len(added_ins)}行" + ("（直線移動だけ）" if only_movl else ""))
        if set(ch.new_job.positions) == set(original.positions):
            parts.append("新しい位置はありません")
    return {
        "target": ch.target, "name": ch.target,
        "before": side(original, ch.block), "after": side(ch.new_job, ch.new_block),
        "added": list(range(ch.before_count + 1, ch.after_count + 1)),
        "summary": "。".join(parts),
    }


def _comparison_dict(c: Comparison, ch: jbi.ChangeProposal, s) -> dict:
    if ch.kind == "repeat":
        after, before = api.figure_data(ch.new_job), api.figure_data(s.original)
        return {
            "kind": "repeat", "target": c.target, "line_no": c.line_number, "heading": c.heading,
            "before_line": "", "after_line": "",
            "before_value": c.before_value, "after_value": c.after_value, "delta": c.delta,
            "diff": [{"sign": sign, "line_no": n, "text": t} for sign, n, t in c.diff],
            "affected": list(c.affected), "warnings": list(c.warnings), "blocked_reason": c.blocked_reason,
            "heard": s.candidate_utterance,
            "repeat": {"positions": list(ch.block.positions), "before_count": ch.before_count,
                       "after_count": ch.after_count},
            # 図：変更前と変更後の区間の線（回の番号つき）。位置は変えないので、変更後の位置は変更前と同じ
            "before_segments": _with_cycles(before["segments"], ch.block),
            "after_segments": _with_cycles(after["segments"], ch.new_block),
            "after_positions": after["positions"], "moved": None, "before_xyz": None, "after_xyz": None,
            "sequence": _repeat_sequence(s.original, ch),  # 動きの並びのカード・差分の要約（feat/demo-3d）
        }
    d = {
        "kind": c.kind, "target": c.target, "line_no": c.line_number,
        "before_line": c.before_line, "after_line": c.after_line,
        "before_value": c.before_value, "after_value": c.after_value, "delta": c.delta,
        "affected": list(c.affected), "warnings": list(c.warnings),
        "blocked_reason": c.blocked_reason,
        "heard": s.candidate_utterance,  # 認識した文字（または打った文字）
    }
    if ch.kind == "shift":
        fig = api.figure_data(ch.new_job)
        name = c.target.split()[0]
        d.update(after_positions=fig["positions"], after_segments=fig["segments"], moved=name,
                 before_xyz=_xyz(s.original, name), after_xyz=_xyz(ch.new_job, name))  # 変更前後の X・Y・Z（プログラムが読んだ値）
    return d


def _facts(f: ut.UtteranceFacts | None) -> dict | None:
    if f is None:
        return None
    return {
        "text": f.raw,
        "kinds": list(f.kinds),
        "positions": list(f.positions),
        "bad_positions": list(f.bad_positions),
        "amounts": [a.text for a in f.amounts],
        "unclear_amounts": list(f.unclear_amounts),
        "axes": list(f.axes),
        # 軸の文字で言った軸・軸の値の増減で言った軸・上下の言葉から補った Z（区別して持つ。CHECKER_026 再修正）
        "explicit_axes": list(f.explicit_axes), "value_axes": list(f.value_axes), "vertical": f.vertical,
        "sides": list(f.sides),
        "refers_to_selection": f.refers_to_selection,
        "requested_value": f.requested_value(),
        "stop_reasons": list(f.stop_reasons),
        "unknown_words": list(f.unknown_words),  # 読み取りで使わなかった言葉（移動・速度の指示なら聞き返す）
        "negations": list(f.negations),  # 打ち消しの言葉（あれば止める）
        "undo_negations": list(f.undo_negations),  # 元に戻す言葉の打ち消し（「キャンセルはやめて」など。あれば止める）
        # 2026-11-03 夜の決定（docs/UNKNOWN_WORDS_PLAN.md 7章）
        "question": f.question, "question_text": f.question_text,
        "repeat": list(f.repeat) if f.repeat else None,
        "place_ref": f.place_ref, "place_as_destination": f.place_as_destination,
        "relative_dir": list(f.relative_dir),
    }


def _log_entries(log: list) -> list[dict]:
    """作業ログ。候補の直後に同じ文の「結果」が続くときは1つにする（同じ行が2回出ないように）。"""
    out: list[dict] = []
    for k, t in log:
        if k == "結果" and out and out[-1]["kind"] == "候補" and out[-1]["text"] == t:
            continue
        out.append({"kind": k, "text": t})
    return out


def _args_text(args: dict) -> str:
    return "、".join(f"{k}={v}" for k, v in args.items()) or "（引数なし）"
