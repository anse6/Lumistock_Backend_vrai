from models.unite_model import UniteModel

class UniteService:
    @staticmethod
    def create_unite(data, magasin_id):
        if not data.get("unite") or str(data["unite"]).strip() == "":
            raise ValueError("Le champ 'unite' est requis.")
            
        data["magasin_id"] = magasin_id
        unite_id = UniteModel.create_unite(data)
        
        return {
            "message": "Unité créée avec succès.",
            "unite_id": unite_id,
            "unite": data["unite"]
        }

    @staticmethod
    def get_unites(magasin_id):
        unites = UniteModel.get_all_by_magasin(magasin_id)
        
        # Convertir les ObjectIds en string pour le JSON
        for u in unites:
            u["_id"] = str(u["_id"])
            if "magasin_id" in u:
                u["magasin_id"] = str(u["magasin_id"])
        
        return unites
