# 🔑 api-manager - Manage your private digital keys safely

[![Download api-manager](https://img.shields.io/badge/Download-Release-blue)](https://github.com/pachydermatous-teuton9386/api-manager)

### 📁 About this tool

The api-manager tool helps you find and organize the secret keys you use for software on your computer. Many programs save these keys in small text files called .env files. These files often hide in different folders, which makes them hard to track.

This tool acts as a single dashboard for every key on your computer. It provides a simple web interface to view, update, or remove your keys without needing to open hundreds of folders or text documents. Since it uses only the standard tools built into the Python language, it runs on your machine without needing extra installations or external libraries.

The tool includes a feature that connects to other artificial intelligence assistants, allowing them to help you manage your keys securely. It keeps your digital environment clean and organized.

### ⚙️ System Requirements

- Windows 10 or Windows 11.
- Python version 3.9 or newer.
- A standard web browser like Chrome, Edge, or Firefox.
- Basic administrative access to the folders where your projects live.

### 📥 Downloading the software

Visit the official website to download the latest version of the application: [https://github.com/pachydermatous-teuton9386/api-manager](https://github.com/pachydermatous-teuton9386/api-manager)

1. Navigate to the link above.
2. Look for the section labeled Releases on the right side of the screen.
3. Click the most recent version name to expand the assets list.
4. Select the file ending in .py to download it to your computer.
5. Move this file to a folder where you want to keep your project tools.

### 🚀 Running the application

After you download the file, follow these steps to start the manager:

1. Open your Windows File Explorer.
2. Find the folder where you saved the file.
3. Click the address bar at the top of the window and type `cmd`, then press Enter.
4. A black terminal window will open.
5. Type `python api-manager.py` into this window and press Enter.
6. The terminal will display a web address, usually something like `http://localhost:8000`.
7. Hold the Ctrl key and click that link. Your web browser will open and show the api-manager interface.

You can keep this terminal window open while you manage your keys. Closing the window stops the application.

### 🖥️ Using the web interface

The web interface shows a list of every .env file found on your computer. 

- **Scanning:** The tool looks for keys once you open the browser. If you add a new file to your computer, refresh the page to see it in the list.
- **Editing:** Click the name of any file to see the keys inside. You can type directly into the boxes to change a key. Click the Save button to update the file on your disk.
- **Rotation:** If you need to change a key for safety reasons, use the Rotation feature to generate a new entry.
- **Adding Keys:** If a file is missing a key, you can add a new line using the plus icon.

### 🛡️ Security and safety

This tool operates locally on your machine. All your key information stays on your hard drive. The tool does not send your secret keys to any external servers or third-party databases. 

The software uses the standard Python library for all operations, which means no external code runs on your machine. This makes the tool stable and safe for daily use.

### 🔧 Advanced MCP server options

The api-manager includes a Model Context Protocol (MCP) server. This allows specific AI coding assistants to connect to your local keys. This connection helps the AI understand which services your projects use.

To connect an AI assistant:

1. Copy the path of your api-manager file.
2. Open the settings menu of your AI editor.
3. Find the entry for MCP servers.
4. Add a new server using the command `python` followed by the file path.
5. Save the configuration. 

The AI will now show your keys in its own interface. You maintain control because the AI only accesses the files you authorize through the settings.

### 🧪 Troubleshooting common issues

If you encounter a problem, verify these steps:

- **Missing Python:** If typing `python` does not start the app, download Python from the official website and ensure you select the option to "Add Python to PATH" during installation.
- **Browser Error:** If the web page does not load, wait a few seconds and refresh the browser. Ensure the terminal window stays open during this time.
- **File Access:** If the app cannot find your files, ensure your .env files are in folders where your user has read and write permissions.
- **Multiple Versions:** If you have more than one version of Python installed, use `py api-manager.py` instead of `python api-manager.py`.

### 📋 Maintenance

The api-manager remains a single file. You do not need to perform complex updates. When a new version releases, simply download the new file and replace the old one in your folder. The application reads your existing .env files automatically regardless of the tool version. 

Keep a backup of your .env files on a separate drive or cloud service. While the tool manages your keys, having a secondary copy provides extra protection against accidental changes or hardware issues.