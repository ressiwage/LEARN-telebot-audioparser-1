import os
import site
import sys

def find_whisper_transcribe_path():
    # Ищем в site-packages текущего интерпретатора
    for site_package in site.getsitepackages():
        whisper_path = os.path.join(site_package, 'whisper', 'transcribe.py')
        if os.path.exists(whisper_path):
            return whisper_path
    
    # Проверяем пользовательские site-packages
    user_site = site.getusersitepackages()
    whisper_path = os.path.join(user_site, 'whisper', 'transcribe.py')
    if os.path.exists(whisper_path):
        return whisper_path
    
    # Если не нашли, возвращаем None
    return None

def modify_transcribe_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if len(lines) < 493:
            print(f"Ошибка: файл содержит только {len(lines)} строк, а требуется изменить 493-ю")
            return False
        
        
        # Заменяем строку, сохраняя отступ
        lines[492] = """            yield f"{round((pbar.n/pbar.total)*100, 2)}%; frame {pbar.n} of {pbar.total}"\n """
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        return True
    except Exception as e:
        print(f"Ошибка при изменении файла: {e}")
        return False

def main():
    print("Поиск файла transcribe.py в пакете whisper...")
    transcribe_path = find_whisper_transcribe_path()
    
    if not transcribe_path:
        print("Не удалось найти файл transcribe.py в установленном пакете whisper")
        print("Убедитесь, что whisper установлен в текущем окружении Python")
        return
    
    print(f"Найден файл: {transcribe_path}")
    print("Изменение строки 493 на 'return False'...")
    
    if modify_transcribe_file(transcribe_path):
        print("Файл успешно изменён!")
    else:
        print("Не удалось изменить файл")

if __name__ == "__main__":
    main()