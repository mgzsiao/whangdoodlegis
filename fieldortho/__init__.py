"""WhangDoodleGIS - offline drone mapping, map georeferencing and hazard scenarios for field teams."""

__version__ = "0.4.0"
APP_NAME = "WhangDoodleGIS"
AUTHOR = "Migz Siao"
# Earlier names of the app: their settings folders are migrated on first start
OLD_APP_NAMES = ("WangDoodletheGIS", "FieldOrtho")

# Full disclaimer: installer accept page (LICENSE-NOTICE.txt), About box, every export README
DISCLAIMER_PARAGRAPHS = (
    f"{APP_NAME} is developed by {AUTHOR} and is provided as is, without warranty. The accuracy of its "
    "maps, elevation models and other outputs depends on the data entered and the settings used.",
    "Flood and landslide models and hazard scenarios are user-run simulations. They do not replace "
    "expert knowledge, or the official hazard maps and assessments of mapping and scientific institutions "
    "such as MGB, NAMRIA, DOST-PAGASA and DOST-PHIVOLCS. Local terrain awareness, professional judgement "
    "and the user's own discretion remain essential. Verify results in the field before using them in any "
    "official decision.",
    f"{APP_NAME} and its developer are not responsible for any misinformation, decision or consequence "
    "arising from the use or sharing of its results.",
)
DISCLAIMER = " ".join(DISCLAIMER_PARAGRAPHS)
DISCLAIMER_SHORT = (f"Developed by {AUTHOR}. Results depend on the accuracy of the data entered. "
                    "Careful judgement and discretion are advised.")
