public class CharSequenceBug {
  public static boolean main(String s) {
    CharSequence target = "b";
    String replaced = "";
    if (target.length() == 1) replaced = s.replace('b', 'c');
    return replaced.indexOf('b') != -1;
  }
}
