"""
CodexHamurabbi — Translations
Add a language: copy any block, give it a new code, translate values.
"""

LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "ru": "Русский",
    "lg": "Luganda",
}

STRINGS = {
    "en": {
        "row_today":     "Today",
        "row_week":      "7 days",
        "row_sessions":  "Sessions",
        "tokens_suffix": "tokens",
        "sessions_sfx":  "sessions",
        "menu_compact":  "→ Compact mode",
        "menu_full":     "→ Full mode",
        "menu_refresh":  "↺  Refresh now",
        "menu_interval": "⏰  Interval",
        "menu_opacity":  "👁  Opacity",
        "menu_language": "🌐  Language",
        "menu_close":    "✕  Close",
        "int_1m":   "1 min",
        "int_5m":   "5 min",
        "int_10m":  "10 min",
        "int_30m":  "30 min",
    },
    "fr": {
        "row_today":     "Aujourd'hui",
        "row_week":      "7 jours",
        "row_sessions":  "Sessions",
        "tokens_suffix": "tokens",
        "sessions_sfx":  "sessions",
        "menu_compact":  "→ Mode compact",
        "menu_full":     "→ Mode complet",
        "menu_refresh":  "↺  Actualiser",
        "menu_interval": "⏰  Intervalle",
        "menu_opacity":  "👁  Opacité",
        "menu_language": "🌐  Langue",
        "menu_close":    "✕  Fermer",
        "int_1m":   "1 min",
        "int_5m":   "5 min",
        "int_10m":  "10 min",
        "int_30m":  "30 min",
    },
    "es": {
        "row_today":     "Hoy",
        "row_week":      "7 días",
        "row_sessions":  "Sesiones",
        "tokens_suffix": "tokens",
        "sessions_sfx":  "sesiones",
        "menu_compact":  "→ Modo compacto",
        "menu_full":     "→ Modo completo",
        "menu_refresh":  "↺  Actualizar",
        "menu_interval": "⏰  Intervalo",
        "menu_opacity":  "👁  Opacidad",
        "menu_language": "🌐  Idioma",
        "menu_close":    "✕  Cerrar",
        "int_1m":   "1 min",
        "int_5m":   "5 min",
        "int_10m":  "10 min",
        "int_30m":  "30 min",
    },
    "ru": {
        "row_today":     "Сегодня",
        "row_week":      "7 дней",
        "row_sessions":  "Сессий",
        "tokens_suffix": "токенов",
        "sessions_sfx":  "сессий",
        "menu_compact":  "→ Компактный",
        "menu_full":     "→ Полный режим",
        "menu_refresh":  "↺  Обновить",
        "menu_interval": "⏰  Интервал",
        "menu_opacity":  "👁  Прозрачность",
        "menu_language": "🌐  Язык",
        "menu_close":    "✕  Закрыть",
        "int_1m":   "1 мин",
        "int_5m":   "5 мин",
        "int_10m":  "10 мин",
        "int_30m":  "30 мин",
    },
    "lg": {
        "row_today":     "Leero",
        "row_week":      "Ennaku 7",
        "row_sessions":  "Emikolo",
        "tokens_suffix": "ebikomo",
        "sessions_sfx":  "emikolo",
        "menu_compact":  "→ Entono",
        "menu_full":     "→ Enzijuvu",
        "menu_refresh":  "↺  Ddamu",
        "menu_interval": "⏰  Ebbanga",
        "menu_opacity":  "👁  Okwolesebwa",
        "menu_language": "🌐  Olulimi",
        "menu_close":    "✕  Galawo",
        "int_1m":   "1 eddakiika",
        "int_5m":   "5 eddakiika",
        "int_10m":  "10 eddakiika",
        "int_30m":  "30 eddakiika",
    },
}


def get(lang: str, key: str, **kwargs):
    val = (STRINGS.get(lang) or STRINGS["en"]).get(key)
    if val is None:
        val = STRINGS["en"].get(key, key)
    if isinstance(val, str) and kwargs:
        return val.format(**kwargs)
    return val
