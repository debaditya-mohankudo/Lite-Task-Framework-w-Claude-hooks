"""Single source of truth for tool→domain and tool→skill mappings."""
import re

TOOL_DOMAIN_MAP: dict[str, str] = {
    "aq__":        "astrology",
    "gochar__":    "astrology",
    "astrology__": "astrology",
    "panchang__":  "astrology",
    "planets__":   "astrology",
    "market__":    "market-intel",
    "vault__":     "vault",
    "vault_rag__": "vault",
    "memory__":    "global",
    "calendar__":  "macos",
    "contacts__":  "macos",
    "imessage__":  "macos",
    "mail__":      "macos",
    "reminders__": "macos",
    "notes__":     "macos",
    "music__":     "macos",
    "safari__":    "macos",
    "system__":    "macos",
    "sound__":     "macos",
    "vpn__":       "macos",
    "podcasts__":  "macos",
    "time__":      "macos",
}

TOOL_SKILL_MAP: dict[str, str] = {
    "aq__current_dasha":             "astrology-current-dasha",
    "aq__predict_events":            "astrology-event-prediction",
    "aq__predict_events_batch":      "astrology-event-prediction",
    "aq__upcoming_transitions":      "astrology-current-dasha",
    "aq__dasha_timeline":            "astrology-current-dasha",
    "aq__pd_timeline":               "astrology-current-dasha",
    "aq__aspects_on_house":          "local-mac-astrology-gochar",
    "aq__planets":                   "local-mac-astrology-gochar",
    "aq__strength_summary":          "local-mac-astrology-gochar",
    "aq__yogas":                     "local-mac-astrology-gochar",
    "aq__navamsha":                  "local-mac-astrology-gochar",
    "aq__vargottama":                "local-mac-astrology-gochar",
    "aq__neecha_bhanga":             "local-mac-astrology-gochar",
    "aq__pushkar_navamsa":           "local-mac-astrology-gochar",
    "gochar__summary":               "local-mac-astrology-gochar",
    "gochar__house":                 "local-mac-astrology-gochar",
    "gochar__dasha_tally":           "local-mac-astrology-gochar",
    "astrology__tarabala":           "local-mac-astrology-gochar",
    "astrology__d9":                 "local-mac-astrology-gochar",
    "astrology__d10":                "local-mac-astrology-gochar",
    "astrology__d12":                "local-mac-astrology-gochar",
    "astrology__mahadasha":          "astrology-current-dasha",
    "panchang__today":               "panchang-analysis",
    "panchang__date":                "panchang-analysis",
    "panchang__rahukaal":            "panchang-analysis",
    "planets__today":                "local-mac-astrology-gochar",
    "planets__date":                 "local-mac-astrology-gochar",
    "planets__lagna":                "local-mac-astrology-gochar",
    "market__gold_regime_history":   "market-intel-gold",
    "market__gold_regime_projection":"market-intel-gold",
    "contacts__search":              "local-mac-contacts",
    "imessage__send":                "local-mac-imessage",
    "imessage__read":                "local-mac-imessage",
    "calendar__add_event":           "local-mac-calendar",
    "calendar__list_events":         "local-mac-calendar",
    "calendar__get_events_by_date":  "local-mac-calendar",
    "calendar__get_upcoming_events": "local-mac-calendar",
    "reminders__create":             "local-mac-reminders",
    "reminders__list":               "local-mac-reminders",
    "reminders__complete":           "local-mac-reminders",
    "notes__read":                   "local-mac-notes",
    "notes__add":                    "local-mac-notes",
    "notes__list":                   "local-mac-notes",
    "mail__read":                    "local-mac-mail",
    "mail__list_mailboxes":          "local-mac-mail",
    "music__play":                   "local-mac-music",
    "music__search_play":            "local-mac-music",
    "music__now_playing":            "local-mac-music",
    "safari__navigate":              "local-mac-safari",
    "safari__read":                  "local-mac-safari",
    "vpn__connect":                  "local-mac-surfshark",
    "vpn__disconnect":               "local-mac-surfshark",
    "vpn__status":                   "local-mac-surfshark",
    "time__now":                     "local-mac-time",
    "time__alarm":                   "local-mac-time",
    "time__wait":                    "local-mac-time",
}

_MCP_PREFIX = re.compile(r"^mcp__[^_]+__")


def strip_mcp_prefix(tool_name: str) -> str:
    """'mcp__local-mac__vault__read' → 'vault__read'"""
    return _MCP_PREFIX.sub("", tool_name)


def infer_domain(short_name: str) -> str:
    for prefix, domain in TOOL_DOMAIN_MAP.items():
        if short_name.startswith(prefix):
            return domain
    return "global"


def infer_skill(short_name: str) -> str:
    return TOOL_SKILL_MAP.get(short_name, "")
