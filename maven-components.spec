Name:		maven-components
Version:	3.10.0
Release:	1
Summary:	Maven and Resolver jars as system Maven artifacts
Group:		Development/Java
License:	Apache-2.0
URL:		https://maven.apache.org/
BuildArch:	noarch

Source0:	maven-components.py

BuildRequires:	python
BuildRequires:	sysjar
BuildRequires:	maven >= 1:3.10.0-2
Requires:	maven >= 1:3.10.0-2

%description
Index of the jars in the maven package. One record per Maven coordinate
under /usr/share/sysjar, pointing at the jar Maven already ships. Plugins
can compile against maven-core, the plugin API and Resolver without a
second copy of those jars.

%install
python %{SOURCE0} %{_datadir}/maven/lib %{_datadir}/maven/toolchain %{buildroot} %{_builddir}/maven-components.files

%files -f %{_builddir}/maven-components.files
