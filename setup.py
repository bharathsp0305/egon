from setuptools import setup, find_packages

# with open("requirements.txt") as f:
#     required = f.read().splitlines()


setup(
    name="generate-dailies",
    version="0.1.0",
    author="Bharath Dhanabalan",
    author_email="bharath.dhanabalan@phantom-fx.com",
    description="generates Daily movie files from a given folder of movies based on the configuration file",
    packages=find_packages(
        exclude=["__pycache__", "build", "dist", "generate_dailies.egg-info"]
    ),
    entry_points={
        "console_scripts": [
            "generate_dailies=generate_dailies.daily:main",
        ],
    },
    # install_requires=required,
    python_requires=">=3.6",
    package_data={"generate_dailies": ["configs/*", "fonts/**", "utils/*"]},
)
