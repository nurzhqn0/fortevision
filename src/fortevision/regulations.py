"""Kazakhstan banking cash-discipline rules that can be checked visually.

Every citation below was checked against the official text (adilet.zan.kz,
current edition as of October 2026). Only clauses whose wording directly
supports the visual check are cited.

Sources:
1. Постановление Правления Национального Банка РК от 29 ноября 2019 года № 231
   «Об утверждении Правил осуществления кассовых операций и операций по инкассации
   банкнот, монет и ценностей в банках второго уровня...» (рег. № 19680).
2. Постановление Правления Национального Банка РК от 24 февраля 2020 года № 14
   «Об утверждении Правил организации охраны и устройства помещений банков второго
   уровня...» (рег. № 20075), с изменениями от 24.12.2024 № 88.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

DOC_231 = (
    "Правила осуществления кассовых операций и операций по инкассации банкнот, монет и "
    "ценностей в банках второго уровня (утв. постановлением Правления НБ РК от 29.11.2019 № 231)"
)
URL_231 = "https://adilet.zan.kz/rus/docs/V1900019680"

DOC_14 = (
    "Правила организации охраны и устройства помещений банков второго уровня "
    "(утв. постановлением Правления НБ РК от 24.02.2020 № 14)"
)
URL_14 = "https://adilet.zan.kz/rus/docs/V2000020075"


@dataclass
class RegulationEntry:
    key: str
    title_ru: str
    regulation_doc: str
    regulation_clause: str
    source_url: str
    legal_norm: str  # Short faithful summary of the cited clause
    default_description: str
    visual_indicators: List[str]  # What the VLM must actually see


REGULATIONS_DB: Dict[str, RegulationEntry] = {
    "unauthorized_person": RegulationEntry(
        key="unauthorized_person",
        title_ru="Постороннее лицо в кассе (кассовом узле)",
        regulation_doc=DOC_14,
        regulation_clause="п. 8; п. 16, пп. 1)",
        source_url=URL_14,
        legal_norm=(
            "Кассовый узел изолируется от других помещений одним закрывающимся служебным входом; "
            "каждая касса имеет отдельный вход с закрывающейся на замок дверью."
        ),
        default_description=(
            "В рабочей зоне кассира (за стойкой/внутри кассы) находится второй человек, "
            "не являющийся кассиром."
        ),
        visual_indicators=[
            "второй человек (не кассир) стоит или ходит внутри кассы рядом с кассиром или за его спиной, у стола или у сейфа",
            "человек заходит в кассу и подходит к рабочему столу/кассиру",
        ],
    ),
    "cashier_absent": RegulationEntry(
        key="cashier_absent",
        title_ru="Кассир покинул рабочее место во время обслуживания",
        regulation_doc=DOC_231,
        regulation_clause="п. 22",
        source_url=URL_231,
        legal_norm="Кассовому работнику не допускается покидать рабочее место до окончания обслуживания клиента.",
        default_description=(
            "Кассир встаёт и уходит от рабочего места, когда клиент находится у кассы "
            "и обслуживание не завершено."
        ),
        visual_indicators=[
            "кассир встаёт, уходит от стола и выходит из кассы, а на столе остаются деньги, сумка или другие ценности",
            "кассир выходит из кассы, не убрав деньги и ценности в сейф",
        ],
    ),
    "cash_unattended": RegulationEntry(
        key="cash_unattended",
        title_ru="Наличные деньги/ценности оставлены открыто без контроля",
        regulation_doc=DOC_231,
        regulation_clause="п. 20",
        source_url=URL_231,
        legal_norm=(
            "На рабочем месте кассира находятся только наличные деньги текущего клиента; все ранее "
            "принятые наличные деньги хранятся в закрывающихся на ключ устройствах "
            "(металлический шкаф, сейф, тележка, ящики стола, тумба)."
        ),
        default_description=(
            "Пачки банкнот или сумка с ценностями лежат открыто на столе без контроля кассира "
            "(кассир отвернулся/отошёл) либо вне закрывающегося хранилища."
        ),
        visual_indicators=[
            "на столе остаются пачки денег, купюры или сумка с ценностями, а кассира у стола нет",
            "сейф открыт или деньги лежат на краю стола, кассир отвернулся/отошёл",
        ],
    ),
    "safe_left_open": RegulationEntry(
        key="safe_left_open",
        title_ru="Сейф/ящик с наличными оставлен открытым",
        regulation_doc=DOC_231,
        regulation_clause="п. 20",
        source_url=URL_231,
        legal_norm=(
            "Ранее принятые наличные деньги хранятся в устройствах на рабочем месте, "
            "закрывающихся на ключ (сейф, шкаф, ящики стола, тумба)."
        ),
        default_description="Дверца сейфа или денежный ящик открыты, кассир при этом отошёл или отвернулся.",
        visual_indicators=[
            "дверца сейфа/тумбы распахнута, внутри видны деньги, кассир не работает с ним",
        ],
    ),
    "personal_items": RegulationEntry(
        key="personal_items",
        title_ru="Личные вещи на рабочем месте в кассе",
        regulation_doc=DOC_231,
        regulation_clause="п. 12",
        source_url=URL_231,
        legal_norm=(
            "Хранение личных вещей работников банка осуществляется вне кассового узла; допускается "
            "внос только лекарств, медизделий и средств связи без функции фото- и видеофиксации."
        ),
        default_description=(
            "На рабочем столе кассира лежат личные вещи (верхняя одежда, сумка, напиток/еда)."
        ),
        visual_indicators=[
            "на кассовом столе куртка, личная сумка, стакан/бутылка с напитком, еда",
            "кассир ест или пьёт за рабочим столом рядом с деньгами",
        ],
    ),
    "phone_use": RegulationEntry(
        key="phone_use",
        title_ru="Смартфон (устройство с камерой) в кассе",
        regulation_doc=DOC_231,
        regulation_clause="п. 12",
        source_url=URL_231,
        legal_norm=(
            "В кассовый узел допускается вносить средства связи только без функции фото- и видеофиксации."
        ),
        default_description="Кассир держит в руках или использует смартфон на рабочем месте.",
        visual_indicators=[
            "в руках кассира смартфон, кассир смотрит в экран",
        ],
    ),
    "manual_counting": RegulationEntry(
        key="manual_counting",
        title_ru="Пересчёт без проверки подлинности на оборудовании",
        regulation_doc=DOC_231,
        regulation_clause="п. 9; п. 19, пп. 2)",
        source_url=URL_231,
        legal_norm=(
            "Банкноты принимаются с полистным пересчётом и проверкой на подлинность и платёжность; "
            "банк обеспечивает наличие оборудования для определения подлинности банкнот."
        ),
        default_description=(
            "Кассир пересчитывает пачку банкнот вручную, не пропуская её через счётно-проверочную машину/детектор."
        ),
        visual_indicators=[
            "кассир держит пачку купюр в руках/в воздухе или раскладывает купюры по столу и пересчитывает вручную",
            "купюры не кладутся в счётную машинку (чёрное устройство на краю стола)",
        ],
    ),
}


def get_regulation(key_or_name: str) -> Optional[RegulationEntry]:
    """Retrieve regulation entry by key or fuzzy title match."""
    if not key_or_name:
        return None

    cleaned = key_or_name.lower().strip()
    if cleaned in REGULATIONS_DB:
        return REGULATIONS_DB[cleaned]

    # Keyword matching
    for key, reg in REGULATIONS_DB.items():
        if key in cleaned:
            return reg
        if any(w in cleaned for w in reg.title_ru.lower().split() if len(w) > 4):
            return reg

    return None


def build_rules_prompt() -> str:
    """Render the rule catalogue for the VLM prompt (single source of truth)."""
    lines = []
    for i, reg in enumerate(REGULATIONS_DB.values(), 1):
        indicators = "; ".join(reg.visual_indicators)
        lines.append(f"{i}. {reg.key} — {reg.title_ru}. Признаки на видео: {indicators}.")
    return "\n".join(lines)


def format_violation_explanation(
    violation_type_or_key: str,
    custom_observation: Optional[str] = None,
) -> Dict[str, str]:
    """Generate structured explanation and regulatory citation for API output."""
    reg = get_regulation(violation_type_or_key)

    if reg is None:
        # Unknown category: report what was seen, cite only the general cash rules document
        return {
            "type": violation_type_or_key or "Возможное нарушение кассовой дисциплины",
            "description": (
                f"Что видно: {custom_observation or 'действие, требующее проверки сотрудником'}. "
                f"Нормативный источник: {DOC_231} ({URL_231}). "
                "Требуется проверка человеком."
            ),
            "regulation": DOC_231,
        }

    observed = (custom_observation or reg.default_description).strip().rstrip(".")
    description = (
        f"Что видно: {observed}. "
        f"Почему это может быть нарушением: {reg.legal_norm} "
        f"Нормативный источник: {reg.regulation_doc}, {reg.regulation_clause} ({reg.source_url})."
    )

    return {
        "type": reg.title_ru,
        "description": description,
        "regulation": f"{reg.regulation_doc}, {reg.regulation_clause}",
    }
