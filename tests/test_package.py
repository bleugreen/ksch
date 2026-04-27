import ksch


def test_package_exports_version() -> None:
    assert isinstance(ksch.__version__, str)
    assert ksch.__version__
