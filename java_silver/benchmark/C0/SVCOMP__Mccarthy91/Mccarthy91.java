public class Mccarthy91 {
  public static int f(int n) {
    if (n > 100) return n - 10;
    else return f(f(n + 11));
  }
}
