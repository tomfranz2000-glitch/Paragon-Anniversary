-- ===========================================================================
-- Paragon: milestone-1000 title compatibility row.
--
-- Custom spell rows previously kept here now belong to the unified
-- Tools/paragon_client_patch.py generator and sql/content/01_paragon_content.sql.
-- Title 200 still needs this compatibility override because the origin server
-- had it baked into CharTitles.dbc while a fresh AzerothCore host does not.
--
-- Apply to: acore_world       (after 01_paragon_content.sql)
-- Then:     RESTART THE WORLDSERVER -- chartitles_dbc is merged at startup.
-- Safe to re-run: every statement is DELETE + INSERT on an explicit id.
-- ===========================================================================

DELETE FROM `chartitles_dbc` WHERE `ID` = 200;
INSERT INTO `chartitles_dbc`
    (`ID`, `Condition_ID`,
     `Name_Lang_enUS`, `Name_Lang_Mask`,
     `Name1_Lang_enUS`, `Name1_Lang_Mask`,
     `Mask_ID`)
VALUES
    (200, 0, 'Paragon %s', 16712190, 'Paragon %s', 16712190, 143);
