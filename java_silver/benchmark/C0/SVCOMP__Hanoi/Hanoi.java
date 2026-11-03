class Hanoi {

    static int counter = 0;

    static int hanoi(int n) {
        if (n == 1) {
          return 1;
        }
        return 2 * (hanoi(n - 1)) + 1;
    }
    
    static void applyHanoi(int n, int from, int to, int via) {
        if (n == 0) {
          return;
        }
        // increment the number of steps
        counter++;
        applyHanoi(n - 1, from, via, to);
        applyHanoi(n - 1, via, to, from);
    }
}