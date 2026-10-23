from marshmallow import Schema, fields, validate

class ChatMessageSchema(Schema):
    """Schéma pour un message de chat"""
    id = fields.Str(dump_only=True, attribute='_id')
    conversation_id = fields.Str(dump_only=True)
    role = fields.Str(required=True, validate=validate.OneOf(['user', 'assistant']))
    content = fields.Str(required=True, validate=validate.Length(min=1))
    timestamp = fields.DateTime(dump_only=True)

class ChatRequestSchema(Schema):
    """Schéma pour une requête de chat"""
    message = fields.Str(required=True, validate=validate.Length(min=1, max=5000))
    conversation_id = fields.Str(allow_none=True)

class ChatResponseSchema(Schema):
    """Schéma pour une réponse de chat"""
    response = fields.Str(required=True)
    conversation_id = fields.Str(required=True)
    message_id = fields.Str(required=True)
    format = fields.Str(load_default='markdown')

class ChatConversationSchema(Schema):
    """Schéma pour une conversation"""
    id = fields.Str(dump_only=True, attribute='_id')
    user_id = fields.Str(dump_only=True)
    magasin_id = fields.Str(dump_only=True)
    title = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)
    message_count = fields.Int(dump_only=True)

class ConversationDetailSchema(Schema):
    """Schéma pour une conversation avec ses messages"""
    id = fields.Str(dump_only=True, attribute='_id')
    title = fields.Str()
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)
    messages = fields.List(fields.Nested(ChatMessageSchema))

class CreateConversationSchema(Schema):
    """Schéma pour créer une nouvelle conversation"""
    title = fields.Str(validate=validate.Length(min=1, max=200), load_default="Nouvelle conversation")

class UpdateConversationSchema(Schema):
    """Schéma pour mettre à jour une conversation"""
    title = fields.Str(required=True, validate=validate.Length(min=1, max=200))
