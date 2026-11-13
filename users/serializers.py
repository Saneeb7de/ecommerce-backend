from rest_framework import serializers
from .models import User, Address
from django.contrib.auth.password_validation import validate_password # for password conditions



def validate_person_name(value):
    """
    Validates that a name contains real letters and no digits or symbols.
    Allows spaces, hyphens, and apostrophes (e.g. 'Mary-Jane O'Connor').
    """
    clean_value = value.strip()
    if len(clean_value) < 2:
        raise serializers.ValidationError("Name must be at least 2 characters long.")
    # 1. Reject if any digits exist
    if any(char.isdigit() for char in clean_value):
        raise serializers.ValidationError("Name cannot contain numbers.")

    # 2. Allow only letters, spaces and apostrophes
    if not all(char.isalpha() or char.isspace() or char in ".'" for char in clean_value):
        raise serializers.ValidationError("Name can only contain letters, spaces, periods, and apostrophes.")
    return clean_value 


#register api serializer
class UserRegistrationSerializer(serializers.ModelSerializer):

    confirm_password = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["name", "email", "password", "confirm_password"]

    def validate_name(self, value):
        return validate_person_name(value)


    def validate_email(self, value):
        email = value.lower()
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def validate(self, data):
        if data["password"]==data["confirm_password"]:
            return data
        raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

    def create(self, validated_data):
        validated_data.pop("confirm_password")
        user = User.objects.create_user(
            email=validated_data["email"],
            name=validated_data["name"],
            password=validated_data["password"],
        )
        return user
    

#login api serializer
class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


#send, verify, resend OTP api serializers
class OTPSendSerializer(serializers.Serializer):
    email = serializers.EmailField()


class OTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6, min_length=6)


#forgot password api serializer
class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

#reset password api serializer
class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data["new_password"] == data["confirm_password"]:
            return data
        raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

#profile serializers

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email", "phone_number", "profile_image", "role", "sso_provider", "sso_organization", "is_sso_verified", "created_at"]
        read_only_fields = ["id", "email", "role", "sso_provider", "sso_organization", "is_sso_verified", "created_at"]

    def validate_name(self, value):
        return validate_person_name(value)


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        # 1. Passwords must match
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "New passwords do not match"})

        # 2. New password cannot be the same as old password
        if data["old_password"] == data["new_password"]:
            raise serializers.ValidationError({"new_password": "New password cannot be same as your old password"})

        return data


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ["id", "recipient_name", "phone_number", "street_address", "apartment_suite", "city", "state", "postal_code", "country", "address_type", "is_default", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_recipient_name(self, value):
        return validate_person_name(value)


#admin serializers
class AdminCustomerListSerializer(serializers.ModelSerializer):
    """
    Serializer for admin customer directory list.
    """

    class Meta:
        model = User
        fields = ["id", "name", "email", "phone_number", "is_verified", "is_active", "sso_provider", "is_sso_verified", "created_at"]

class AdminCustomerDetailSerializer(serializers.ModelSerializer):
    """
    Detailed customer inspection for Admin, including their saved addresses.
    """

    addresses = AddressSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "email", "phone_number", "profile_image", "role", "is_verified", "is_active", "sso_provider", "sso_organization", "is_sso_verified", "created_at", "updated_at", "addresses"] # Nested addresses array!



class GoogleAuthSerializer(serializers.Serializer):
    """
    Validates incoming Google ID Token from Frontend / Mobile client.
    """

    id_token = serializers.CharField(required=True, trim_whitespace=True)