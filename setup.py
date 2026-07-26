from setuptools import setup
from wheel.bdist_wheel import bdist_wheel


class PlatformWheel(bdist_wheel):
    """Tag wheels that include native search binaries for their platform."""

    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        _python, _abi, platform = super().get_tag()
        return "py3", "none", platform


setup(cmdclass={"bdist_wheel": PlatformWheel})
