"""End-to-end encryption for P2P sync."""

import base64
import os
import secrets
from pathlib import Path

# Try to import cryptography, fall back gracefully
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class CryptoManager:
    """Handles encryption for P2P sync."""

    def __init__(self, key_dir: Path):
        self.key_dir = key_dir
        self.key_dir.mkdir(parents=True, exist_ok=True)

        self._private_key: bytes | None = None
        self._public_key: bytes | None = None
        self._shared_keys: dict[str, bytes] = {}  # peer_id -> derived key

    @property
    def available(self) -> bool:
        """Check if cryptography is available."""
        return CRYPTO_AVAILABLE

    def generate_keys(self) -> None:
        """Generate new X25519 key pair."""
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography not installed")

        private_key = x25519.X25519PrivateKey.generate()
        self._private_key = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

        # Save keys
        self._save_keys()

    def _save_keys(self) -> None:
        """Save keys to disk."""
        if self._private_key:
            (self.key_dir / "private.key").write_bytes(self._private_key)
        if self._public_key:
            (self.key_dir / "public.key").write_bytes(self._public_key)

    def load_keys(self) -> bool:
        """Load existing keys from disk."""
        private_path = self.key_dir / "private.key"
        public_path = self.key_dir / "public.key"

        if private_path.exists() and public_path.exists():
            self._private_key = private_path.read_bytes()
            self._public_key = public_path.read_bytes()
            return True

        return False

    def get_public_key(self) -> bytes:
        """Get public key bytes."""
        if self._public_key is None:
            if not self.load_keys():
                self.generate_keys()
        return self._public_key  # type: ignore

    def get_public_key_b64(self) -> str:
        """Get public key as base64 string."""
        return base64.b64encode(self.get_public_key()).decode()

    def derive_shared_key(self, peer_id: str, peer_public_key: bytes) -> bytes:
        """Derive shared secret using X25519."""
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography not installed")

        if peer_id in self._shared_keys:
            return self._shared_keys[peer_id]

        if self._private_key is None:
            self.load_keys()

        private_key = x25519.X25519PrivateKey.from_private_bytes(self._private_key)  # type: ignore
        peer_key = x25519.X25519PublicKey.from_public_bytes(peer_public_key)

        shared_secret = private_key.exchange(peer_key)

        # Derive encryption key using HKDF
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"one_claude_sync_v1",
            info=b"encryption",
        ).derive(shared_secret)

        self._shared_keys[peer_id] = derived
        return derived

    def encrypt(self, peer_id: str, plaintext: bytes) -> bytes:
        """Encrypt data for specific peer using AES-GCM."""
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography not installed")

        key = self._shared_keys.get(peer_id)
        if key is None:
            raise ValueError(f"No shared key for peer {peer_id}")

        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        return nonce + ciphertext

    def decrypt(self, peer_id: str, ciphertext: bytes) -> bytes:
        """Decrypt data from peer."""
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography not installed")

        key = self._shared_keys.get(peer_id)
        if key is None:
            raise ValueError(f"No shared key for peer {peer_id}")

        nonce = ciphertext[:12]
        encrypted = ciphertext[12:]

        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, encrypted, None)

    def generate_nonce(self) -> str:
        """Generate a random nonce for message replay protection."""
        return secrets.token_hex(16)


class MockCryptoManager:
    """Mock crypto manager for testing without cryptography installed."""

    def __init__(self, key_dir: Path):
        self.key_dir = key_dir
        self._id = secrets.token_hex(8)

    @property
    def available(self) -> bool:
        return True

    def generate_keys(self) -> None:
        pass

    def load_keys(self) -> bool:
        return True

    def get_public_key(self) -> bytes:
        return self._id.encode()

    def get_public_key_b64(self) -> str:
        return base64.b64encode(self.get_public_key()).decode()

    def derive_shared_key(self, peer_id: str, peer_public_key: bytes) -> bytes:
        return b"mock_shared_key_32_bytes_long!!!"

    def encrypt(self, peer_id: str, plaintext: bytes) -> bytes:
        # Simple XOR for mock
        return b"MOCK" + plaintext

    def decrypt(self, peer_id: str, ciphertext: bytes) -> bytes:
        if ciphertext.startswith(b"MOCK"):
            return ciphertext[4:]
        return ciphertext

    def generate_nonce(self) -> str:
        return secrets.token_hex(16)
