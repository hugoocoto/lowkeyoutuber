from align import align_text, pick_takes


def words(text, start=0.0, step=0.4, gap_after=()):
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append({"w": w, "s": round(t, 3), "e": round(t + step - 0.1, 3)})
        t += step + (1.5 if i in gap_after else 0)
    return out


def test_align_keeps_script_spelling_and_times():
    heard = words("hola esto es main punto c")
    got, ratio = align_text("Hola, esto es main.c", heard)
    assert [w["w"] for w in got] == ["Hola,", "esto", "es", "main.c"]
    assert got[0]["s"] == heard[0]["s"] and got[2]["e"] == heard[2]["e"]
    # main.c was heard as three words: placed after "es", inside the speech
    assert heard[2]["e"] <= got[3]["s"] < got[3]["e"] <= heard[-1]["e"]
    assert 0.5 < ratio < 1


def test_align_is_monotonic_with_mishearings():
    heard = words("el operador cegra y conoce funciona así")
    got, _ = align_text("El operador de C que nadie conoce funciona así", heard)
    times = [w["s"] for w in got]
    assert times == sorted(times)
    assert all(w["e"] > w["s"] for w in got)


def test_pick_takes_keeps_last_retake():
    lines = [{"id": "a", "text": "Hoy vamos a ver punteros en C."},
             {"id": "b", "text": "Un puntero guarda una dirección de memoria."}]
    # line a said badly, then again well; then line b
    said = ("Hoy vamos a ver puntos. Hoy vamos a ver punteros en C. "
            "Un puntero guarda una dirección de memoria.")
    w = words(said, gap_after=(4, 11))
    chosen, report = pick_takes(lines, w)
    assert [r["status"] for r in report] == ["ok", "ok"]
    assert report[0]["takes"] == 2 and report[0]["discarded"] == 1
    assert chosen[0]["start"] == w[5]["s"]           # the second attempt
    assert chosen[1]["start"] == w[12]["s"]


def test_pick_takes_reports_missing_line():
    lines = [{"id": "a", "text": "Primera línea del guion."}, {"id": "b", "text": "Esta nunca se dijo."}]
    chosen, report = pick_takes(lines, words("Primera línea del guion."))
    assert report[0]["status"] == "ok" and report[1]["status"] == "missing" and chosen[1] is None
