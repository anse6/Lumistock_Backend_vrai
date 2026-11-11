"""Chiffre d'affaires HT d'une vente : articles seuls, sans TVA ni frais de retour."""
from services.stats_staff_service import _ca_ht


def test_vente_entierement_retournee_ne_compte_pas_ses_frais():
    # 1 palette rendue sur 1 : il ne reste que 170 de frais de retour dans le total.
    assert _ca_ht({"montant_produits": 0.0, "montant_total": 170.0, "frais_retour": 170.0}) == 0.0


def test_vente_normale_et_ancienne_vente():
    assert _ca_ht({"montant_produits": 3400.0, "montant_total": 4054.5, "montant_tva": 654.5}) == 3400.0
    # Ancienne vente sans montant_produits : total − TVA − dette − frais.
    assert _ca_ht({"montant_total": 1300.0, "montant_tva": 100.0, "frais_retour": 200.0}) == 1000.0
