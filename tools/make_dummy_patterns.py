"""合成（架空）のテスト用ジョブ samples/dummy/PAT_*.JBI を作る。

- すべて samples/dummy/DEMO_PLACE.JBI をもとに、このリポジトリで新しく作った架空の値。実機のジョブではない。
- Shift_JIS（cp932）のファイルも、エディタで保存せず、ここでバイト列として書く。
- 日付などの変わる値は使わないので、何度実行しても同じバイト列になる。
- 一覧と期待結果は samples/dummy/PATTERNS.md、確かめるテストは tests/test_dummy_patterns.py。

使い方:
    python tools/make_dummy_patterns.py          # samples/dummy/ に書き出す
    python tools/make_dummy_patterns.py --check  # 書き出さずに、今のファイルと同じか確かめる
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "samples" / "dummy"

# ASCII のファイルに入れるコメント（文字コードの試験と混ぜないため ASCII にする）
ASCII_NOTE = "'SYNTHETIC TEST DATA - NOT A REAL ROBOT JOB"
# Shift_JIS のファイルに入れるコメント
SJIS_NOTE = "'合成テスト用（実機のジョブではない）"

ROBOT_HEADER = ["///TOOL 0", "///POSTYPE ROBOT", "///RECTAN", "///RCONF 0,0,0,0,0,0,0,0"]

# DEMO_PLACE.JBI をもとにした架空の位置（戻りの位置 C00006 は C00000 と同じ値で別の名前にする）
BASE_POS = [
    "C00000=450.000,-200.000,350.000,180.00,0.00,0.00",
    "C00001=450.000,-200.000,150.000,180.00,0.00,0.00",
    "C00002=450.000,-200.000,250.000,180.00,0.00,0.00",
    "C00003=450.000,190.000,250.000,180.00,0.00,0.00",
    "C00004=450.000,190.000,120.000,180.00,0.00,0.00",
    "C00005=450.000,190.000,250.000,180.00,0.00,0.00",
    "C00006=450.000,-200.000,350.000,180.00,0.00,0.00",
]

BASE_MOVES = [
    "MOVL C00000 V=200.0",
    "MOVL C00001 V=80.0",
    "DOUT OT#(1) ON",
    "TIMER T=0.50",
    "MOVL C00002 V=80.0",
    "MOVL C00003 V=250.0",
    "MOVL C00004 V=80.0",
    "TIMER T=3.00",
    "DOUT OT#(1) OFF",
    "TIMER T=0.50",
    "MOVL C00005 V=80.0",
    "MOVL C00006 V=200.0",
]

# パルス形式の架空の値（6軸の整数）
PULSE_POS = [
    "C00000=1200,-35000,21000,0,-48000,500",
    "C00001=1200,-41000,9000,0,-52000,500",
    "C00002=1200,-38000,15000,0,-50000,500",
    "C00003=-9800,-38000,15000,0,-50000,-9200",
    "C00004=-9800,-42000,7000,0,-53000,-9200",
    "C00005=-9800,-38000,15000,0,-50000,-9200",
    "C00006=1200,-35000,21000,0,-48000,500",
]


def moves(replace: dict[str, str] | None = None,
          after: dict[str, list[str]] | None = None) -> list[str]:
    """BASE_MOVES の行を置き換えたり、ある行の後ろに行を足したりする。"""
    replace = replace or {}
    after = after or {}
    out: list[str] = []
    for line in BASE_MOVES:
        out.append(replace.get(line, line))
        out.extend(after.get(line, []))
    return out


def job(name: str, pos: list[str], inst: list[str], *, note: str = ASCII_NOTE,
        comm: str | None = None, extra_inst_header: list[str] | None = None,
        eol: str = "\n", encoding: str = "ascii") -> bytes:
    npos = sum(1 for line in pos if re.match(r"C\d{5}=", line))
    lines = ["/JOB", f"//NAME {name}", "//POS", f"///NPOS {npos},0,0,0,0,0", *pos, "//INST",
             "///DATE 2026/11/02 14:40"]
    if comm is not None:
        lines.append(f"///COMM {comm}")
    lines += ["///ATTR SC,RW", "///GROUP1 RB1", *(extra_inst_header or []), "NOP", note, *inst, "END"]
    return "".join(line + eol for line in lines).encode(encoding)


def build() -> dict[str, bytes]:
    """ファイル名 → バイト列。"""
    files: dict[str, bytes] = {}
    robot = ROBOT_HEADER + BASE_POS

    # 1. 正常な MOVL だけのジョブ
    files["PAT_MOVL_OK.JBI"] = job("PAT_MOVL_OK", robot, moves())

    # 2. PL=0（置く位置 C00004 へ入る命令と出る命令に PL=0 を明記）
    files["PAT_PL0.JBI"] = job("PAT_PL0", robot, moves(replace={
        "MOVL C00004 V=80.0": "MOVL C00004 V=80.0 PL=0",
        "MOVL C00005 V=80.0": "MOVL C00005 V=80.0 PL=0",
    }))

    # 3. PL>0（C00002 へ入る命令に PL=3）
    files["PAT_PL_GT0.JBI"] = job("PAT_PL_GT0", robot, moves(replace={
        "MOVL C00002 V=80.0": "MOVL C00002 V=80.0 PL=3",
    }))

    # 4. CR 指定（C00002 へ入る命令に CR=10.0）
    files["PAT_CR.JBI"] = job("PAT_CR", robot, moves(replace={
        "MOVL C00002 V=80.0": "MOVL C00002 V=80.0 CR=10.0",
    }))

    # 5. MOVJ（最初と最後の移動が関節補間）
    files["PAT_MOVJ.JBI"] = job("PAT_MOVJ", robot, moves(replace={
        "MOVL C00000 V=200.0": "MOVJ C00000 VJ=25.00",
        "MOVL C00006 V=200.0": "MOVJ C00006 VJ=25.00",
    }))

    # 6. MOVC（C00002〜C00004 を円弧で通る）
    files["PAT_MOVC.JBI"] = job("PAT_MOVC", robot, moves(replace={
        "MOVL C00002 V=80.0": "MOVC C00002 V=80.0",
        "MOVL C00003 V=250.0": "MOVC C00003 V=250.0",
        "MOVL C00004 V=80.0": "MOVC C00004 V=80.0",
    }))

    # 7. ROBOT 形式（ツール番号 1 の指定つき）
    files["PAT_POSTYPE_ROBOT.JBI"] = job(
        "PAT_POSTYPE_ROBOT",
        ["///TOOL 1", "///POSTYPE ROBOT", "///RECTAN", "///RCONF 0,0,0,0,0,0,0,0"] + BASE_POS,
        moves())

    # 8. PULSE 形式
    files["PAT_POSTYPE_PULSE.JBI"] = job(
        "PAT_POSTYPE_PULSE", ["///TOOL 0", "///POSTYPE PULSE", "///PULSE"] + PULSE_POS, moves())

    # 9. 位置ごとに形式が混ざる（C00000〜C00002 ROBOT、C00003〜C00004 PULSE、C00005〜C00006 ROBOT）
    files["PAT_POSTYPE_MIXED.JBI"] = job(
        "PAT_POSTYPE_MIXED",
        ROBOT_HEADER + BASE_POS[0:3]
        + ["///POSTYPE PULSE", "///PULSE"] + PULSE_POS[3:5]
        + ["///POSTYPE ROBOT", "///RECTAN"] + BASE_POS[5:7],
        moves())

    # 10. 同じ位置を複数の命令が使う（近づく点と離れる点を共用）
    files["PAT_SHARED_POS.JBI"] = job(
        "PAT_SHARED_POS", ROBOT_HEADER + BASE_POS[0:5],
        [
            "MOVL C00000 V=200.0",
            "MOVL C00002 V=200.0",
            "MOVL C00001 V=80.0",
            "DOUT OT#(1) ON",
            "TIMER T=0.50",
            "MOVL C00002 V=80.0",
            "MOVL C00003 V=250.0",
            "MOVL C00004 V=80.0",
            "TIMER T=3.00",
            "DOUT OT#(1) OFF",
            "TIMER T=0.50",
            "MOVL C00003 V=80.0",
            "MOVL C00000 V=200.0",
        ])

    # 11. 位置の意味を変える特殊命令（SFTON・SFTOF）
    files["PAT_SFTON.JBI"] = job("PAT_SFTON", robot, moves(after={
        "MOVL C00000 V=200.0": ["SFTON P000"],
        "MOVL C00005 V=80.0": ["SFTOF"],
    }))

    # 12・13. Shift_JIS（cp932）と日本語コメント（LF 版と CRLF 版）
    #   「ソ」「表」「能」は2バイト目が 0x5C（\）になる。半角カナ、全角空白、///COMM の日本語を含む
    sjis_inst = moves(after={
        "MOVL C00001 V=80.0": ["'ﾊﾝﾄﾞ ｦ ﾄｼﾞﾙ"],
        "MOVL C00003 V=250.0": ["'表示の確認　ソの字と能の字"],
    })
    for name, eol in (("PAT_SJIS_COMMENT", "\n"), ("PAT_SJIS_COMMENT_CRLF", "\r\n")):
        files[f"{name}.JBI"] = job(
            name, robot, sjis_inst, note=SJIS_NOTE, comm="合成テスト　ソ表能ｶﾅ",
            eol=eol, encoding="cp932")

    # 14. cp932 で読んで書き戻すとバイト列が変わる文字（NEC特殊文字の「≒」0x8790 → 書き戻すと 0x81E0）
    data = job("PAT_SJIS_NOROUNDTRIP", robot, moves(after={
        "MOVL C00003 V=250.0": ["'@@"],
    }), note=SJIS_NOTE, encoding="cp932")
    files["PAT_SJIS_NOROUNDTRIP.JBI"] = data.replace(b"'@@", b"'\x87\x90", 1)

    # 15. 未知の命令（架空の名前 ZZFAKE）
    files["PAT_UNKNOWN_INST.JBI"] = job("PAT_UNKNOWN_INST", robot, moves(after={
        "TIMER T=3.00": ["ZZFAKE T=1.00"],
    }))

    # 16〜18. 解析できない位置データ
    bad = list(BASE_POS)
    bad[2] = "C00002=450.000,-200.000,ABC,180.00,0.00,0.00"
    files["PAT_BAD_POS_NONNUM.JBI"] = job("PAT_BAD_POS_NONNUM", ROBOT_HEADER + bad, moves())

    short = list(BASE_POS)
    short[2] = "C00002=450.000,-200.000"
    files["PAT_BAD_POS_SHORT.JBI"] = job("PAT_BAD_POS_SHORT", ROBOT_HEADER + short, moves())

    dup = BASE_POS[0:3] + ["C00002=450.000,-200.000,260.000,180.00,0.00,0.00"] + BASE_POS[3:]
    files["PAT_BAD_POS_DUP.JBI"] = job("PAT_BAD_POS_DUP", ROBOT_HEADER + dup, moves())

    # 19・20. ///LVARS（数字をカンマで区切った行／読めない値の行）
    files["PAT_LVARS.JBI"] = job("PAT_LVARS", robot, moves(),
                                 extra_inst_header=["///LVARS 0,0,0,0,0,0,0,0"])
    files["PAT_LVARS_BAD.JBI"] = job("PAT_LVARS_BAD", robot, moves(),
                                     extra_inst_header=["///LVARS X,Y"])
    return files


def main(argv: list[str]) -> int:
    files = build()
    if "--check" in argv:
        bad = [n for n, b in files.items()
               if not (OUT_DIR / n).exists() or (OUT_DIR / n).read_bytes() != b]
        for n in bad:
            print(f"違う、または無い: {n}")
        return 1 if bad else 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for n, b in files.items():
        (OUT_DIR / n).write_bytes(b)
    print(f"{len(files)} 個のファイルを {OUT_DIR} に書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
