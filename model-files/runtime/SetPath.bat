:: SetPath.bat
:: Utility to set the path.  Used in RunModel as well as RunMain and RunNodeX. 

:: The commpath
SET COMMPATH=X:\COMMPATH
if "%COMPUTER_PREFIX%" == "WIN-" (  SET COMMPATH=D:\COMMPATH)
if %computername%==MODEL2-A      (  set COMMPATH=E:\Model2A-Share\COMMPATH)
if %computername%==MODEL2-B      (  set COMMPATH=E:\Model2B-Share\COMMPATH)
if %computername%==MODEL2-C      (  set COMMPATH=E:\Model2C-Share\COMMPATH)
if %computername%==MODEL2-D      (  set COMMPATH=E:\Model2D-Share\COMMPATH)
if %computername%==MODEL3-A      (  set COMMPATH=E:\Model3A-Share\COMMPATH)
if %computername%==MODEL3-B      (  set COMMPATH=E:\Model3B-Share\COMMPATH)

:: The location of the 64-bit java development kit
set JAVA_PATH=C:\Program Files\Java\jdk1.8.0_181

:: The location of the GAWK binary executable files
set GAWK_PATH=X:\UTIL\Gawk
if "%COMPUTER_PREFIX%" == "WIN-" (
  set GAWK_PATH=C:\Software\Gawk
)

:: The location of R and R libraries
set R_HOME=C:\Program Files\R\R-3.5.2
set R_LIB=C:\Users\mtcpb\Documents\R\win-library\3.5
if "%COMPUTER_PREFIX%" == "WIN-" (
  set R_LIB=C:\Users\Administrator\Documents\R\win-library\3.5
)
if "%computername%" == "MODEL3-A" (
  set R_HOME=C:\Program Files\R\R-4.2.1
  set R_LIB=C:\Users\mtcpb\AppData\Local\R\win-library\4.2
)
if "%computername%" == "MODEL3-B" (
  set R_HOME=C:\Program Files\R\R-4.2.1
  set R_LIB=C:\Users\mtcpb\AppData\Local\R\win-library\4.2
)

:: The location of the RUNTPP executable from Citilabs
set TPP_PATH=C:\Program Files\Citilabs\CubeVoyager;C:\Program Files\Citilabs\VoyagerFileAPI

:: The location of python
set PYTHON_PATH=C:\Python27
if "%computername%" == "MODEL3-A" (
  set PYTHON_PATH=C:\Users\mtcpb\.conda\envs\tm15-py27
)
if "%computername%" == "MODEL3-B" (
  set CONDA_ENV_PATH=C:\Users\mtcpb\.conda\envs
  set ENV_NAME="tm15-py27"
  rem set ENV_NAME=tm15-py27
  rem set PYTHON_PATH=%CONDA_ENV_PATH%\%ENV_NAME%
  set PYTHON_PATH=C:\Users\mtcpb\.conda\envs\tm15-py27
  set CONDA_PATH=C:\ProgramData\Anaconda3\condabin
)
:: The location of the MTC.JAR file
set RUNTIME=CTRAMP/runtime

:: Add these variables to the PATH environment variable, moving the current path to the back
set PATH=%RUNTIME%;%JAVA_PATH%/bin;%TPP_PATH%;%GAWK_PATH%/bin;%PYTHON_PATH%
if "%computername%" == "MODEL3-B" (
  set PATH=%RUNTIME%;%JAVA_PATH%/bin;%TPP_PATH%;%GAWK_PATH%/bin;%CONDA_ENV_PATH%;%CONDA_PATH%
)

::  Set the Java classpath (locations where Java needs to find configuration and JAR files)
set CLASSPATH=%RUNTIME%/config;%RUNTIME%;%RUNTIME%/config/jppf-2.4/jppf-2.4-admin-ui/lib/*;%RUNTIME%/mtc.jar

