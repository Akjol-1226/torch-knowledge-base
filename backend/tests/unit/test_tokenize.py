import app.core.retrieval.tokenize as tk


def test_write_domain_terms_roundtrip_keeps_dotted_model(tmp_path):
    dpath = tmp_path / "domain_dict_auto.txt"
    tk.write_domain_terms(["DEMO1-0603-2X1-50V-0.10n", "NPD9001"], dpath)
    written = dpath.read_text(encoding="utf-8").splitlines()
    # 词典每行第一段（词本身）必须是完整型号，不能含空格被切断
    first_col = [ln.split("\t")[0] for ln in written if ln.strip()]
    assert "DEMO1-0603-2X1-50V-0.10n" in first_col
    assert "NPD9001" in first_col


def test_write_domain_terms_dedup(tmp_path):
    dpath = tmp_path / "d.txt"
    tk.write_domain_terms(["NPD9001"], dpath)
    tk.write_domain_terms(["NPD9001", "HJ900001"], dpath)
    lines = [l for l in dpath.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert sum(1 for l in lines if l.startswith("NPD9001")) == 1
    assert any(l.startswith("HJ900001") for l in lines)


def test_tokenize_keeps_model_after_loading_dict(tmp_path):
    dpath = tmp_path / "d.txt"
    tk.write_domain_terms(["DEMO1-0603-2X1-50V-0.10n"], dpath)
    tk.load_dict(dpath)
    toks = tk.tokenize("DEMO1-0603-2X1-50V-0.10n 的耐压是多少")
    assert "DEMO1-0603-2X1-50V-0.10n" in toks


def test_load_dict_alone_registers_hyphenated_term(tmp_path):
    # 手写 TAB 词典（模拟入库产物），不经过 write_domain_terms 的 add_word，
    # 只靠 load_dict 把含连字符的型号注册为整词
    dpath = tmp_path / "d.txt"
    dpath.write_text("ZZQ-9988-7X\t10\tn\n", encoding="utf-8")
    n = tk.load_dict(dpath)
    assert n >= 1
    toks = tk.tokenize("查 ZZQ-9988-7X 参数")
    assert "ZZQ-9988-7X" in toks
