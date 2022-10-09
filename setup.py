from setuptools import setup

setup(name='btinhibitor',
      version='0.1',
      description='A program to disable the screensaver when some Bluetooth device is nearby.',
      url='http://github.com/tommie/btinhibitor',
      author='Tommie Gannert',
      author_email='tommie@gannert.se',
      license='MIT',
      install_requires=[
          'dbus-python',
          'PyGObject',
      ],
      packages=[
          'btinhibitor',
      ],
      entry_points=dict(
          console_scripts=[
              'btinhibitor=btinhibitor.cli:main',
          ]),
      zip_safe=False)
