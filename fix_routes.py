import sys
import os

path = r'c:\Users\Hp\Documents\anse\backend1\tkdata-backend\routes\livraison_routes.py'
if os.path.exists(path):
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Keeping only the lines before the broken addition
    # Based on previous view_file, line 1144 was "return jsonify..."
    # So we keep up to indices 0..1143 (which is 1144 lines)
    if len(lines) > 1144:
        lines = lines[:1144]
        
    new_route = """
@livraison_bp.route("/livraisons/accorder-ristourne/<string:livraison_id>", methods=["POST"])
@jwt_required()
@role_required("livreur", "manager", "admin")
def accorder_ristourne(livraison_id):
    \"\"\"
    Permet à un livreur ou un manager d'accorder une ristourne sur une livraison existante.
    \"\"\"
    data = request.get_json()
    montant = data.get("montant")

    if montant is None:
        return jsonify({"message": "Le montant de la ristourne est requis"}), 400

    current_user_id = get_jwt_identity()
    result, status_code = accorder_ristourne_service(current_user_id, livraison_id, montant)
    return jsonify(result), status_code
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
        f.write(new_route)
    print("File fixed successfully")
else:
    print("File not found")
