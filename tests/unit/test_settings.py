"""Unit tests for the configuration system."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app.config.constants import DEFAULT_IDLE_THRESHOLD_SECONDS
from app.config.settings import AppSettings, LocalConfig, ServerPolicy
from pydantic import ValidationError


def test_local_defaults() -> None:
    settings = LocalConfig(_env_file=None)
    assert settings.mode == "local"
    assert settings.log_level == "INFO"
    assert settings.data_dir is not None


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EM_MODE", "api")
    monkeypatch.setenv("EM_LOG_LEVEL", "DEBUG")
    settings = LocalConfig(_env_file=None)
    assert settings.mode == "api"
    assert settings.log_level == "DEBUG"


def test_data_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("EM_DATA_DIR", str(target))
    settings = LocalConfig(_env_file=None)
    assert settings.data_dir == target


def test_data_dir_expands_tilde() -> None:
    settings = LocalConfig(data_dir="~/monitoring", _env_file=None)
    assert str(settings.data_dir).startswith(str(Path("~").expanduser()))


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError, match="log_level"):
        LocalConfig(log_level="VERBOSE", _env_file=None)


def test_server_policy_defaults() -> None:
    policy = ServerPolicy()
    assert policy.screenshot_interval_seconds == 60
    assert policy.idle_threshold_seconds == DEFAULT_IDLE_THRESHOLD_SECONDS


def test_screenshot_interval_minimum_enforced() -> None:
    with pytest.raises(ValueError, match="at least"):
        ServerPolicy(screenshot_interval_seconds=59)
    # Boundary accepted.
    assert (
        ServerPolicy(screenshot_interval_seconds=60).screenshot_interval_seconds == 60
    )


def test_app_settings_composition(app_settings: AppSettings) -> None:
    assert app_settings.screenshot_interval_seconds == 60
    assert app_settings.idle_threshold_seconds == 300
    assert app_settings.mode == "local"
    assert app_settings.subdir("logs") == app_settings.data_dir / "logs"


class TestDotEnvLookup:
    """`.env` must be found even when the app is launched from elsewhere.

    pydantic-settings resolves a relative ``env_file`` against the current
    working directory, so a config file next to the code is silently ignored
    when the app is started from a shortcut or a different shell — api mode then
    falls back to local, and the employee is told their password is wrong.
    """

    def test_the_source_tree_is_searched_absolutely(self) -> None:
        from app.config.settings import _env_file_sources

        sources = _env_file_sources()

        assert Path(sources[0]).is_absolute()
        assert Path(sources[0]).name == ".env"
        assert Path(sources[0]).parent == Path(__file__).resolve().parents[2]

    def test_the_working_directory_copy_overrides_it(self) -> None:
        from app.config.settings import _env_file_sources

        # Last source wins in pydantic-settings, so the more specific
        # location must be the relative one. (This suite is not frozen, so no
        # exe-adjacent override is appended after it.)
        assert _env_file_sources()[-1] == ".env"

    def test_a_dot_env_in_the_working_directory_is_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".env").write_text("EM_MODE=api\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EM_MODE", raising=False)

        assert LocalConfig(_env_file=".env").mode == "api"

    def test_a_dot_env_can_still_turn_api_mode_back_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".env").write_text("EM_MODE=local\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EM_MODE", raising=False)

        assert LocalConfig(_env_file=".env").mode == "local"


class TestFrozenBundleEnvLookup:
    """A packaged build has no source tree, so it needs its own lookup.

    ``dist/`` used to ship no config at all: the app resolved ``EM_MODE`` to
    its ``local`` default, signed in through the dummy provider, and refused
    every real account — which the user sees as a wrong password. The bundle
    therefore carries a ``.env`` (installer/build.spec) and a ``.env`` beside
    the ``.exe`` overrides it, so a real deployment is a config change rather
    than a rebuild.
    """

    @staticmethod
    def _pretend_frozen(
        monkeypatch: pytest.MonkeyPatch, bundle: Path, exe_dir: Path
    ) -> None:
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "EmployeeMonitoring.exe"))

    def test_bundle_and_exe_are_searched_when_frozen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config.settings import _env_file_sources

        bundle = tmp_path / "_internal"
        exe_dir = tmp_path / "EmployeeMonitoring"
        self._pretend_frozen(monkeypatch, bundle, exe_dir)

        sources = _env_file_sources()

        assert str(bundle / ".env") in sources
        assert str(exe_dir / ".env") in sources

    def test_exe_override_ranks_above_the_bundled_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config.settings import _env_file_sources

        # Later sources win in pydantic-settings. The .env beside the .exe is
        # the deliberate per-deployment file, so it has to outrank everything —
        # including a stray source tree or working directory.
        self._pretend_frozen(monkeypatch, tmp_path / "_internal", tmp_path / "app")

        sources = _env_file_sources()

        assert sources[-1] == str(tmp_path / "app" / ".env")
        assert sources.index(str(tmp_path / "app" / ".env")) > sources.index(
            str(tmp_path / "_internal" / ".env")
        )

    def test_the_bundled_default_is_actually_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config.settings import _env_file_sources

        bundle = tmp_path / "_internal"
        bundle.mkdir()
        (bundle / ".env").write_text(
            "EM_MODE=api\nEM_API_BASE_URL=http://127.0.0.1:8000/api/v1\n"
        )
        self._pretend_frozen(monkeypatch, bundle, tmp_path / "app")
        monkeypatch.delenv("EM_MODE", raising=False)
        monkeypatch.delenv("EM_API_BASE_URL", raising=False)

        config = LocalConfig(_env_file=_env_file_sources())

        assert config.mode == "api"
        assert config.api_base_url == "http://127.0.0.1:8000/api/v1"

    def test_a_dot_env_beside_the_exe_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config.settings import _env_file_sources

        bundle = tmp_path / "_internal"
        exe_dir = tmp_path / "app"
        bundle.mkdir()
        exe_dir.mkdir()
        (bundle / ".env").write_text("EM_API_BASE_URL=http://127.0.0.1:8000/api/v1\n")
        (exe_dir / ".env").write_text("EM_API_BASE_URL=https://prod.test/api/v1\n")
        self._pretend_frozen(monkeypatch, bundle, exe_dir)
        monkeypatch.delenv("EM_API_BASE_URL", raising=False)

        config = LocalConfig(_env_file=_env_file_sources())

        assert config.api_base_url == "https://prod.test/api/v1"

    def test_nothing_frozen_means_no_bundle_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config.settings import _env_file_sources

        monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        sources = _env_file_sources()

        assert sources == (
            str(Path(__file__).resolve().parents[2] / ".env"),
            ".env",
        )


class TestShippedEnvTemplate:
    """The template that ships inside the bundle must be a working api config.

    Bundling a template whose keys are all commented out would reproduce the
    exact silent fallback this change exists to remove.
    """

    def test_the_bundled_template_selects_api_mode(self) -> None:
        from app.config.settings import _ENV_FILE

        template = Path(__file__).resolve().parents[2] / f"{_ENV_FILE}.example"
        text = template.read_text(encoding="utf-8")

        assert "EM_MODE=api" in text
        assert "EM_API_BASE_URL=http" in text


class TestApiBaseUrlSafety:
    """The base URL is bundled into a distributed binary and read from DNS.

    Two things must never happen: a credential riding along inside it, and the
    employee's password or session token crossing the network in cleartext.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/api/v1",
            "http://127.0.0.2:8000/api/v1",
            "http://localhost:8000/api/v1",
            "https://monitoring.company.com/api/v1",
        ],
    )
    def test_acceptable_urls(self, url: str) -> None:
        assert LocalConfig(api_base_url=url).api_base_url == url

    def test_plaintext_http_to_a_real_host_is_rejected(self) -> None:
        """The one genuinely dangerous misconfiguration.

        Over http the password and the bearer token are readable by anyone on
        the path, so this must fail loudly rather than start and look fine.
        """
        with pytest.raises(ValidationError, match="https"):
            LocalConfig(api_base_url="http://monitoring.company.com/api/v1")

    def test_credentials_embedded_in_the_url_are_rejected(self) -> None:
        from app.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="username or password"):
            LocalConfig(api_base_url="https://admin:s3cr3t@host/api/v1")

    @pytest.mark.parametrize(
        "url",
        [
            "https://host/api/v1?api_key=abc123",
            "https://host/api/v1?access_token=zzz999",
            "https://host/api/v1?client_secret=q",
        ],
    )
    def test_credential_like_query_parameters_are_rejected(self, url: str) -> None:
        from app.core.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="credential-like"):
            LocalConfig(api_base_url=url)

    def test_ordinary_query_parameters_are_allowed(self) -> None:
        url = "https://host/api/v1?region=eu&format=json"
        assert LocalConfig(api_base_url=url).api_base_url == url

    def test_no_error_message_echoes_the_credential(self) -> None:
        """A field validation error carries ``input_value=<the whole url>``.

        pydantic quotes the value back, so a rejected URL containing a secret
        would put that secret into logs and dialogs. Rejecting before the
        field validator is what keeps it out.
        """
        from app.core.exceptions import ConfigurationError

        for url, secret in (
            ("https://admin:s3cr3t@host/api/v1", "s3cr3t"),
            ("https://host/api/v1?api_key=abc123", "abc123"),
        ):
            with pytest.raises(ConfigurationError) as caught:
                LocalConfig(api_base_url=url)
            assert secret not in str(caught.value)
