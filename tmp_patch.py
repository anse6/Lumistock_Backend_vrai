import os

with open("method.py", "r", encoding="utf-8") as f:
    code = f.read()

# Replace method name
code = code.replace("modifier_livraison_by_manager_service", "modifier_livraison_by_livreur_service")
# Replace stock fetching
code = code.replace('stock_dispo = ProduitModel.get_all_by_magasin(livraison["magasin_id"])', 
                    'stock_dispo = StockLivreurModel.get_stock_livreur(livraison["livreur_id"])')
code = code.replace('stock_map = {str(item["_id"]): item["quantite"] for item in stock_dispo}', 
                    'stock_map = {str(item["produit_id"]): item["quantite"] for item in stock_dispo if "produit_id" in item}')
# Replace stock error message
code = code.replace("dans le magasin", "dans votre stock livreur")

# Replace the restitution/deduction block
old_stock_block = """        # 3. Effectuer la restitution et la déduction réelles
        acteur = {"admin_id": str(livraison.get("manager_id", ""))}
        
        # Restitution
        for old_prod in livraison.get("produits", []):
            ProduitModel.increment_stock(
                produit_id=str(old_prod["produit_id"]),
                quantite=old_prod["quantite"],
                acteur=acteur,
                type_mouvement="restauration modif livraison"
            )
            
        # Déduction
        for new_prod in produits_final:
            ProduitModel.decrement_stock(
                produit_id=str(new_prod["produit_id"]),
                quantite=new_prod["quantite"],
                acteur=acteur,
                type_mouvement="modification livraison manager"
            )"""

new_stock_block = """        # 3. Effectuer la restitution et la déduction dans le stock livreur
        
        # Restitution
        StockLivreurModel.ajouter_stock(livraison["livreur_id"], livraison.get("produits", []))
            
        # Déduction
        StockLivreurModel.retirer_stock(livraison["livreur_id"], produits_final)"""

code = code.replace(old_stock_block, new_stock_block)

file_path = r"c:\Users\ansevernel\Documents\work\Lumistock backend\services\livraison_service.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()
    
# Split by lines, find the line definition of serialize_livraison
lines = content.split('\n')
insert_idx = -1
for i, line in enumerate(lines):
    if line.startswith("def serialize_livraison"):
        insert_idx = i
        break

if insert_idx != -1:
    lines.insert(insert_idx, "\n" + code + "\n")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Successfully patched livraison_service.py")
else:
    print("Could not find serialize_livraison")
