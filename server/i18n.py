"""Server-side messages in English and Turkish.

Everything the server says to the user (notices, quota decisions, health
problems, errors) goes through `t(key, **values)`. The language comes from
the `language` setting: "en", "tr", or "auto", which follows the system
language the macOS app passes in PIXELCREW_SYSTEM_LANGUAGE (or LANG).

Turkish needs the right case ending on agent names ("Claude'a", "Gemini'ye"),
so those forms come from config.AGENTS; English uses plain names.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

LANGUAGES = ("en", "tr")


def system_language() -> str:
    raw = (os.environ.get("PIXELCREW_SYSTEM_LANGUAGE") or os.environ.get("LANG") or "en").lower()
    return "tr" if raw.startswith("tr") else "en"


def lang() -> str:
    from .config import load_settings
    chosen = load_settings().get("language", "auto")
    return chosen if chosen in LANGUAGES else system_language()


M: Dict[str, Dict[str, str]] = {
    # -- run flow ---------------------------------------------------------------
    "plan.already_approved": {
        "en": "The plan was already approved; changes can't be applied to this run.",
        "tr": "Plan zaten onaylandı; değişiklikler bu işe uygulanamaz."},
    "session.resume_failed": {
        "en": "Couldn't continue the previous session; starting a new one.",
        "tr": "Önceki oturum sürdürülemedi, yeni oturum açılıyor."},
    "chat.resume_failed": {
        "en": "The previous conversation is gone; starting a new session.",
        "tr": "Önceki konuşma bulunamadı, yeni oturum açılıyor."},
    "run.no_agents": {
        "en": "No agent is available. Follow the hints on the status lights at the top, or turn on Demo.",
        "tr": "Hiçbir ajan kullanılabilir değil. Üstteki durum ışıklarındaki talimatları uygula ya da Demo'yu aç."},
    "run.alone": {"en": "{name} will do all the work alone", "tr": "tüm işi {name} tek başına yapacak"},
    "run.shared": {"en": "the work will be shared by {names}", "tr": "iş {names} arasında paylaşılacak"},
    "run.missing": {
        "en": "{names} can't be used right now (not installed or not signed in); {rest}.",
        "tr": "{names} şu an kullanılamıyor (giriş ya da kurulum eksik); {rest}."},
    "plan.invalid_json": {"en": "the planner did not return a valid JSON plan",
                          "tr": "planlayıcı geçerli bir JSON planı döndürmedi"},
    "plan.failed": {"en": "Couldn't make a plan ({reason}). The request will run as a single task.",
                    "tr": "Plan çıkarılamadı ({reason}). İş tek görev olarak yürütülecek."},
    "plan.single_summary": {"en": "Running the request as a single task.", "tr": "İstek tek görev olarak yürütülüyor."},
    "plan_review.no_result": {"en": "The plan review gave no result ({reason}); using the draft plan.",
                              "tr": "Plan kontrolü sonuç vermedi ({reason}); taslak plan kullanılıyor."},
    "plan_review.invalid": {"en": "invalid reply", "tr": "geçersiz yanıt"},
    "task.default_title": {"en": "Task {i}", "tr": "Görev {i}"},
    "task.blocked": {"en": "{id} skipped: a task it depends on did not finish.",
                     "tr": "{id} atlandı: bağlı olduğu görev tamamlanamadı."},
    "task.error": {"en": "Error while running {id}: {err}", "tr": "{id} işlenirken hata: {err}"},
    "review.unreadable": {"en": "Couldn't read the review result.", "tr": "İnceleme sonucu okunamadı."},
    "review.no_result": {"en": "The review of {id} gave no result; the task was accepted as is.",
                         "tr": "{id} incelemesi sonuç vermedi; görev olduğu gibi kabul edildi."},
    "review.open_notes": {"en": "{id} still has open review notes (fix round limit reached).",
                          "tr": "{id} için açık kalan inceleme notları var (düzeltme turu sınırına ulaşıldı)."},
    "final.problems": {"en": "The final check left unresolved problems; see the Summary tab.",
                       "tr": "Son kontrolde çözülemeyen sorunlar kaldı; Özet sekmesine bak."},
    "coverage.moved": {"en": "At least one task per agent: {list}.", "tr": "Her ajana en az bir görev: {list} verildi."},
    "coverage.item": {"en": "{id} to {name}", "tr": "{id} {to}"},
    "coverage.idle": {"en": "The request split into {n} tasks; {names} got none this time.",
                      "tr": "İş {n} göreve bölündü; {names} bu işte görev almadı."},
    # -- quota decisions ----------------------------------------------------------
    "quota.hit": {"en": "{old} hit its usage limit", "tr": "{old} limitine takıldı"},
    "takeover.plan": {"en": "{old} hit its usage limit; {new} took over the plan.",
                      "tr": "{old} limitine takıldı; planı {new} devraldı."},
    "takeover.final": {"en": "{old} hit its usage limit; {new} took over the final check.",
                       "tr": "{old} limitine takıldı; son kontrolü {new} devraldı."},
    "takeover.review": {"en": "{old} hit its usage limit; {new} took over the review of {id}.",
                        "tr": "{old} limitine takıldı; {id} kontrolünü {new} devraldı."},
    "takeover.task": {"en": "{old} hit its usage limit; {new} took over {id}.",
                      "tr": "{old} limitine takıldı; {id} işini {new} devraldı."},
    "plan_review.skipped": {"en": "Plan review skipped: {why}.", "tr": "Plan kontrolü atlandı: {why}."},
    "quota.exclude": {"en": "{why}. It sits this run out; {rest}.", "tr": "{why}. Bu işte yer almayacak; {rest}."},
    "quota.exclude.alone": {"en": "{name} does the work alone", "tr": "işi {name} tek başına yapacak"},
    "quota.exclude.shared": {"en": "{names} share the work", "tr": "işi {names} paylaşacak"},
    "quota.tight": {"en": "{why}. Heavy tasks go to {mate}; {name} works one effort level lower.",
                    "tr": "{why}. Ağır görevler {to} verilecek, {name} bir kademe düşük eforla çalışacak."},
    "quota.lead": {"en": "{new} makes the plan this time: {why}.", "tr": "Planı bu sefer {new} yapıyor: {why}."},
    "quota.reassign": {"en": "{id} moved to {mate}: {why}.", "tr": "{id} {to} geçti: {why}."},
    "quota.reassign.heavy": {"en": "{id} is a heavy task; it moved to {mate}: {why}.",
                             "tr": "{id} ağır bir görev, {to} geçti: {why}."},
    "quota.dropped": {"en": "Limit dropped: {why}.", "tr": "Limit düştü: {why}."},
    "review.moved": {"en": "{name} no longer reviews: {why}. {then}",
                     "tr": "Kontrolleri artık {name} yapmayacak: {why}. {then}"},
    "review.moved.others": {"en": "{names} will review instead.", "tr": "Yerine {names} bakacak."},
    "review.moved.self": {"en": "The author will review its own work in a fresh session.",
                          "tr": "Görevi yapan ajan, yeni bir oturumla kendi işini kontrol edecek."},
    "review.moved.mixed": {"en": "{names} will review instead; for the rest the author reviews its own work.",
                           "tr": "Yerine {names} bakacak; kalanlarda görevi yapan ajan kendi işini kontrol edecek."},
    "quota.unknown": {"en": "{name}'s limit is unknown", "tr": "{of} limiti bilinmiyor"},
    "quota.left": {"en": "{name} has {left}% of its {window}limit left", "tr": "{of} {window}limitinde %{left} kaldı"},
    "quota.resets": {"en": ", resets {when}", "tr": ", yenilenme {when}"},
    "window.5 saat": {"en": "5-hour ", "tr": "5 saatlik "},
    "window.hafta": {"en": "weekly ", "tr": "haftalık "},
    "window.gün": {"en": "daily ", "tr": "günlük "},
    "window.ay": {"en": "monthly ", "tr": "aylık "},
    "ask.note": {"en": "{why}; {names} answer this question.", "tr": "{why}; bu soruyu {names} yanıtlıyor."},
    "ask.note.only": {"en": "{why}; only {names} answers this question.", "tr": "{why}; bu soruyu yalnızca {names} yanıtlıyor."},
    # -- http / requests ------------------------------------------------------------
    "req.too_big": {"en": "Request too large.", "tr": "İstek çok büyük."},
    "req.bad_host": {"en": "Invalid Host header.", "tr": "Geçersiz Host başlığı."},
    "req.no_record": {"en": "Record not found.", "tr": "Kayıt bulunamadı."},
    "req.origin": {"en": "This request can only come from the PixelCrew window.",
                   "tr": "Bu istek yalnızca PixelCrew arayüzünden yapılabilir."},
    "req.json": {"en": "Content-Type must be application/json.", "tr": "Content-Type application/json olmalı."},
    "req.not_found": {"en": "Not found.", "tr": "Bulunamadı."},
    "run.empty": {"en": "Write what should be done first.", "tr": "Önce ne yapılacağını yaz."},
    "run.busy": {"en": "A run is in progress. Stop it or wait for it to finish.",
                 "tr": "Şu an bir iş çalışıyor. Önce onu durdur ya da bitmesini bekle."},
    "ask.empty": {"en": "Write your question first.", "tr": "Önce sorunu yaz."},
    "ask.to_list": {"en": "'to' must be a list.", "tr": "'to' bir liste olmalı."},
    "pick.macos_only": {"en": "The folder picker only exists on macOS. Type the path instead.",
                        "tr": "Klasör seçici yalnızca macOS'ta var. Yolu elle yaz."},
    "pick.open": {"en": "The folder picker is already open.", "tr": "Klasör seçme penceresi zaten açık."},
    "pick.prompt": {"en": "Choose the PixelCrew working folder", "tr": "PixelCrew çalışma klasörünü seç"},
    "pick.failed": {"en": "Couldn't open the folder picker: {err}", "tr": "Klasör seçici açılamadı: {err}"},
    "approve.none": {"en": "No run is waiting for approval.", "tr": "Onay bekleyen bir iş yok."},
    "cancel.none": {"en": "No run is in progress.", "tr": "Çalışan bir iş yok."},
    "chat.busy_reset": {"en": "The agents are still answering. Stop them or wait until they finish.",
                        "tr": "Ajanlar hâlâ cevap yazıyor. Önce durdur ya da bitmesini bekle."},
    "chat.not_ready": {"en": "The agent you picked isn't ready. Check the status lights at the top, or turn on Demo.",
                       "tr": "Seçtiğin ajan şu an hazır değil. Üstteki ışıklara bak ya da Demo'yu aç."},
    "chat.busy": {"en": "The agents are answering the previous question. Wait or stop them.",
                  "tr": "Ajanlar önceki soruyu cevaplıyor. Bitmesini bekle ya da durdur."},
    "req.object": {"en": "Expected a JSON object.", "tr": "JSON nesnesi bekleniyordu."},
    "key.format": {"en": "that doesn't look like an API key", "tr": "bu bir API anahtarına benzemiyor"},
    "terminal.none": {"en": "There's no automatic step for this; follow the guide link.",
                      "tr": "Bunun için otomatik adım yok; kılavuz bağlantısını izle."},
    "terminal.title.install": {"en": "PixelCrew: installing {tool}", "tr": "PixelCrew: {tool} kuruluyor"},
    "terminal.title.login": {"en": "PixelCrew: signing in to {tool}", "tr": "PixelCrew: {tool} girişi"},
    "terminal.done": {"en": "Done. Close this window and press Check again in PixelCrew.",
                      "tr": "Tamam. Bu pencereyi kapatıp PixelCrew'da Tekrar kontrol et'e bas."},
    "terminal.failed_step": {"en": "This step didn't finish. Read the message above, then try again.",
                             "tr": "Bu adım tamamlanmadı. Yukarıdaki mesajı okuyup tekrar dene."},
    "health.no_key": {"en": "No API key saved for {name}.", "tr": "{name} için kayıtlı API anahtarı yok."},
    "key.unknown": {"en": "Unknown API key.", "tr": "Bilinmeyen API anahtarı."},
    "key.saved": {"en": "Saved to the Keychain.", "tr": "Anahtar Zinciri'ne kaydedildi."},
    "key.failed": {"en": "Couldn't save to the Keychain: {err}", "tr": "Anahtar Zinciri'ne kaydedilemedi: {err}"},
    "terminal.failed": {"en": "Couldn't open Terminal: {err}", "tr": "Terminal açılamadı: {err}"},
    # -- workspace ---------------------------------------------------------------------
    "ws.empty": {"en": "The working folder can't be empty.", "tr": "Çalışma klasörü boş olamaz."},
    "ws.home": {"en": "The root folder or your whole home folder can't be the working folder. Pick a project folder.",
                "tr": "Kök dizin ya da ev klasörünün tamamı çalışma klasörü olamaz. Bir proje klasörü seç."},
    "ws.not_found": {"en": "Folder not found: {path}", "tr": "Klasör bulunamadı: {path}"},
    "ws.no_parent": {"en": "Parent folder doesn't exist: {path}", "tr": "Üst klasör yok: {path}"},
    "ws.not_dir": {"en": "This is not a folder: {path}", "tr": "Bu bir klasör değil: {path}"},
    # -- health ------------------------------------------------------------------------
    "health.missing": {"en": "{tool} is not installed.", "tr": "{tool} kurulu değil."},
    "health.logged_out": {"en": "{tool} is not signed in.", "tr": "{tool} giriş yapılmamış."},
    "health.check_failed": {"en": "Couldn't check {tool}: {err}", "tr": "{tool} kontrol edilemedi: {err}"},
    "health.gemini_retired": {
        "en": "Gemini CLI no longer accepts personal Google sign-in. Install Antigravity CLI (agy) instead.",
        "tr": "Gemini CLI artık kişisel Google hesabıyla giriş kabul etmiyor. Yerine Antigravity CLI'ı (agy) kur."},
    "auth.api_key": {"en": "API key", "tr": "API anahtarı"},
    "auth.account": {"en": "{who} account", "tr": "{who} hesabı"},
    # -- agent errors ------------------------------------------------------------------
    "agent.timeout": {"en": "Time limit reached.", "tr": "Süre sınırı aşıldı."},
    "agent.timeout_stopping": {"en": "Time limit reached; stopping the process.", "tr": "Süre sınırı aşıldı, süreç durduruluyor."},
    "agent.parse_failed": {"en": "couldn't parse an event: {err}", "tr": "olay ayrıştırılamadı: {err}"},
    "agent.exit_code": {"en": "{tool} exited with code {code}", "tr": "{tool} çıkış kodu {code}"},
    "agent.turn_failed": {"en": "The {tool} turn failed.", "tr": "{tool} turu başarısız oldu."},
    "agent.login_hint": {"en": "{tool} is not signed in. Run `{cmd}` in Terminal.",
                         "tr": "{tool} giriş yapılmamış. Terminalde `{cmd}` çalıştır."},
    "agent.todo": {"en": "to-do list", "tr": "yapılacaklar listesi"},
    "diff.truncated": {"en": "\n… (truncated)", "tr": "\n… (kısaltıldı)"},
}


def t(key: str, **values: Any) -> str:
    entry = M.get(key)
    if not entry:
        return key
    text = entry.get(lang()) or entry["en"]
    try:
        return text.format(**values)
    except (KeyError, IndexError):
        return text


def names(labels: List[str]) -> str:
    """Claude, Codex and Gemini / Claude, Codex ve Gemini"""
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    joiner = " ve " if lang() == "tr" else " and "
    return ", ".join(labels[:-1]) + joiner + labels[-1]
