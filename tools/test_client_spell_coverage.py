import unittest

import paragon_client_patch as patch


class ClientSpellCoverageTests(unittest.TestCase):
    def test_previously_external_spells_are_client_generated(self):
        client_ids = ({row["id"] for row in patch.REWARD_AURAS}
                      | {row["id"] for row in patch.CUSTOM_SPELLS})
        self.assertTrue({1900003, 1900004, 1900005, 1900014} <= client_ids)

    def test_external_spell_semantics_are_preserved(self):
        auras = {row["id"]: row for row in patch.REWARD_AURAS}
        self.assertEqual((31, 49),
                         (auras[1900003]["aura"], auras[1900003]["basepoints"]))
        self.assertEqual((58, 99),
                         (auras[1900004]["aura"], auras[1900004]["basepoints"]))
        self.assertEqual((4, 0),
                         (auras[1900005]["aura"], auras[1900005]["basepoints"]))

        burst = next(row for row in patch.CUSTOM_SPELLS
                     if row["id"] == 1900014)
        self.assertEqual(48078, burst["clone"])
        self.assertEqual(14, burst["overrides"]["EffectRadiusIndex_1"])
        self.assertEqual(0.32, burst["bonus"]["direct"])
        self.assertEqual(0.32, burst["bonus"]["ap"])

    def test_declared_server_only_spells_are_allowed(self):
        server_only = [row["id"] for row in patch.SERVER_SPELLS]
        rows = [(1900003, "client spell")]
        rows.extend((sid, "intentional") for sid in server_only)
        self.assertEqual(
            (1, len(server_only), len(rows)),
            patch.audit_client_spell_coverage(rows, [1900003], server_only))

    def test_every_unexplained_server_spell_is_reported(self):
        with self.assertRaises(ValueError) as caught:
            patch.audit_client_spell_coverage(
                [(1900003, "Paragon Swiftness"),
                 (1900004, "Paragon Aquatic Grace")],
                [], [])
        message = str(caught.exception)
        self.assertIn("1900003 Paragon Swiftness", message)
        self.assertIn("1900004 Paragon Aquatic Grace", message)

    def test_duplicate_and_conflicting_declarations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            patch.audit_client_spell_coverage([], [1900003, 1900003], [])
        with self.assertRaisesRegex(ValueError, "both client-side and server-only"):
            patch.audit_client_spell_coverage([], [1900003], [1900003])


if __name__ == "__main__":
    unittest.main()
