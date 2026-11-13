// Discover the actual installed service menus through Dolphin's own KIO API.
// Never trigger an action or touch the user's application configuration.
#include <KFileItem>
#include <KFileItemActions>
#include <KFileItemListProperties>
#include <QAction>
#include <QApplication>
#include <QDir>
#include <QFile>
#include <QMenu>
#include <QSet>
#include <QTemporaryDir>
#include <QUrl>
#include <iostream>
#include <sys/stat.h>

static QSet<QString> names(const KFileItemList &items)
{
    KFileItemActions actions;
    actions.setItemListProperties(KFileItemListProperties(items));
    QMenu menu;
    actions.addActionsTo(&menu, KFileItemActions::MenuActionSource::Services);
    QSet<QString> result;
    for (const auto action : menu.actions()) {
        if (!action->isSeparator()) {
            result.insert(action->text());
        }
    }
    return result;
}

int main(int argc, char **argv)
{
    QApplication app(argc, argv);
    QTemporaryDir directory;
    if (!directory.isValid()) {
        return 1;
    }
    const auto item = [&](const QString &name, const QString &mime, bool folder = false) {
        const QString path = directory.filePath(name);
        if (folder) {
            if (!QDir().mkpath(path)) {
                qFatal("Could not create fixture directory");
            }
        } else {
            QFile file(path);
            if (!file.open(QIODevice::WriteOnly)) {
                qFatal("Could not create fixture file");
            }
        }
        return KFileItem(QUrl::fromLocalFile(path), mime, folder ? S_IFDIR : S_IFREG);
    };
    const auto pdf = item(QStringLiteral("document.pdf"), QStringLiteral("application/pdf"));
    const auto png = item(QStringLiteral("image.png"), QStringLiteral("image/png"));
    const auto text = item(QStringLiteral("note.txt"), QStringLiteral("text/plain"));
    const auto folder = item(QStringLiteral("folder"), QStringLiteral("inode/directory"), true);
    const QString rename = QStringLiteral("PowerRename…");
    const QString extract = QStringLiteral("Extract Pages to Images");
    const QString resize = QStringLiteral("Resize Pictures…");
    const QList<QPair<KFileItemList, QSet<QString>>> cases = {
        {{pdf}, {rename, extract}},
        {{pdf, pdf}, {rename, extract}},
        {{png}, {rename, resize}},
        {{png, png}, {rename, resize}},
        {{text}, {rename}},
        {{folder}, {rename}},
        {{folder, text}, {rename}},
        {{pdf, png}, {rename}},
        {{KFileItem(QUrl(QStringLiteral("sftp://example.invalid/document.pdf")),
                    QStringLiteral("application/pdf"), S_IFREG)}, {}},
    };
    for (int i = 0; i < cases.size(); ++i) {
        const auto actual = names(cases[i].first);
        if (actual != cases[i].second) {
            std::cerr << "Wrong service menus for fixture " << i << ": ";
            for (const auto &name : actual) {
                std::cerr << name.toStdString() << "; ";
            }
            std::cerr << '\n';
            return 1;
        }
    }
    return 0;
}
