-- ============================================================================
-- Paragon Anniversary - Lockpicking and weapon-skill mastery rewards
-- ============================================================================
-- Forward-only upgrade for existing installations. Current skill values seed
-- the durable high-water ledger with zero pending XP, so only future gains pay.
-- Fist Weapons (473) canonicalizes to Unarmed (162) because AzerothCore can
-- advance both for one attack.
-- ============================================================================

START TRANSACTION;

INSERT INTO `acore_ale`.`paragon_profession_progress`
    (`owner_type`, `owner_id`, `skill_id`, `high_water`, `pending_xp`)
SELECT 1, c.`account`,
    CASE WHEN cs.`skill` = 473 THEN 162 ELSE cs.`skill` END,
    MAX(cs.`value`), 0
FROM `acore_characters`.`character_skills` cs
JOIN `acore_characters`.`characters` c ON c.`guid` = cs.`guid`
WHERE cs.`skill` IN (
    43,44,45,46,54,55,129,136,160,162,164,165,171,172,173,176,
    182,185,186,197,202,226,228,229,333,356,393,473,633,755,773
)
GROUP BY c.`account`, CASE WHEN cs.`skill` = 473 THEN 162 ELSE cs.`skill` END
ON DUPLICATE KEY UPDATE
    `high_water` = GREATEST(`high_water`, VALUES(`high_water`));

INSERT INTO `acore_ale`.`paragon_profession_progress`
    (`owner_type`, `owner_id`, `skill_id`, `high_water`, `pending_xp`)
SELECT 0, cs.`guid`,
    CASE WHEN cs.`skill` = 473 THEN 162 ELSE cs.`skill` END,
    MAX(cs.`value`), 0
FROM `acore_characters`.`character_skills` cs
WHERE cs.`skill` IN (
    43,44,45,46,54,55,129,136,160,162,164,165,171,172,173,176,
    182,185,186,197,202,226,228,229,333,356,393,473,633,755,773
)
GROUP BY cs.`guid`, CASE WHEN cs.`skill` = 473 THEN 162 ELSE cs.`skill` END
ON DUPLICATE KEY UPDATE
    `high_water` = GREATEST(`high_water`, VALUES(`high_water`));

COMMIT;
