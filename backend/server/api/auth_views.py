from rest_framework import serializers, status, views
from rest_framework.response import Response
from django.contrib.auth.models import User
from django.db import IntegrityError

class RegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "email", "password"]
        extra_kwargs = {"password": {"write_only": True, "min_length": 8}}
    def create(self, data):
        return User.objects.create_user(
    username=data["username"],
    email=data["email"],
    password=data["password"]
)

class RegisterView(views.APIView):
    permission_classes, authentication_classes = [], []
    def post(self, request):
        s = RegisterSerializer(data=request.data); s.is_valid(raise_exception=True)
        try:
            u = s.save()
            return Response({"id": u.id, "username": u.username, "email": u.email}, status=201)
        except IntegrityError:
            return Response({"detail": "Username or email already exists"}, status=400)
