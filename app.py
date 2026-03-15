from flask import Flask, request, send_from_directory, render_template, url_for
import os

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


@app.route("/")
def home():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files["file"]

    if file and file.filename.endswith(".apk"):
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
        file.save(filepath)

        download_link = url_for("download_file", filename=file.filename, _external=True)

        return f"""
        APK Uploaded Successfully ✅ <br><br>
        Direct Download Link:<br>
        <a href="{download_link}">{download_link}</a>
        """

    return "Only APK files allowed!"


@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
